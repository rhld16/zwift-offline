#!/usr/bin/env python

import os
import signal
import struct
import sys
import threading
import time
import csv
import json
import math
import random
import itertools
import socketserver
from urllib3 import PoolManager
from http.server import SimpleHTTPRequestHandler
from datetime import datetime, timedelta, timezone
from Crypto.Cipher import AES

import zwift_offline as zo
import udp_node_msgs_pb2
import tcp_node_msgs_pb2
import profile_pb2

from ant.core import driver
from ant.core import node

from usb.core import find

from PowerMeterTx import PowerMeterTx, NETKEY, POWER_SENSOR_ID

antnode = None
power_meter = None

last_state = udp_node_msgs_pb2.PlayerState()

# Values from retail server
# 65 KG 2.5 W/KG - 164 181 131
# 65 KG 4.2 W/KG - 273 301 218
# 75 KG 1.8 W/KG - 135 148 107
# 75 KG 2.0 W/HG - 150 165 119
# 75 KG 2.3 W/KG - 173 190 138
# 75 KG 3.0 W/KG - 225 247 179
# 75 Kg 3.8 W/KG - 285 313 227
# 81 KG 3.3 W/KG - 265 291 211
# 81 KG 1.5 W/KG - 125 137 99
DEFAULT_POWER = 164
INCREASED_POWER = 181
DECREASED_POWER = 131
power = DEFAULT_POWER

def stop_ant():
    if power_meter:
        print("Closing power meter")
        power_meter.close()
        power_meter.unassign()
    if antnode:
        print("Stopping ANT node")
        antnode.stop()

def on_exit(sig, func=None):
    stop_ant()

import win32api
win32api.SetConsoleCtrlHandler(on_exit, True)

if getattr(sys, 'frozen', False):
    # If we're running as a pyinstaller bundle
    SCRIPT_DIR = sys._MEIPASS
    EXE_DIR = os.path.dirname(sys.executable)
    STORAGE_DIR = "%s/storage" % EXE_DIR
    PACE_PARTNERS_DIR = '%s/pace_partners' % EXE_DIR
else:
    SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
    STORAGE_DIR = "%s/storage" % SCRIPT_DIR
    PACE_PARTNERS_DIR = '%s/pace_partners' % SCRIPT_DIR

CDN_DIR = "%s/cdn" % SCRIPT_DIR
CDN_PROXY = os.path.isfile('%s/cdn-proxy.txt' % STORAGE_DIR)

FAKE_DNS_FILE = "%s/fake-dns.txt" % STORAGE_DIR
ENABLE_BOTS_FILE = "%s/enable_bots.txt" % STORAGE_DIR
DISCORD_CONFIG_FILE = "%s/discord.cfg" % STORAGE_DIR
if os.path.isfile(DISCORD_CONFIG_FILE):
    from discord_bot import DiscordThread
    discord = DiscordThread(DISCORD_CONFIG_FILE)
else:
    class DummyDiscord():
        def send_message(self, msg, sender_id=None):
            pass
        def change_presence(self, n):
            pass
    discord = DummyDiscord()

bot_update_freq = 1
pacer_update_freq = 1
simulated_latency = 300 #makes bots animation smoother than using current time
last_pp_updates = {}
last_bot_updates = {}
global_ghosts = {}
ghosts_enabled = {}
online = {}
player_update_queue = {}
global_pace_partners = {}
global_bots = {}
global_news = {} #player id to dictionary of peer_player_id->worldTime
global_relay = {}
global_clients = {}
map_override = {}
climb_override = {}

def sigint_handler(num, frame):
    httpd.shutdown()
    httpd.server_close()
    tcpserver.shutdown()
    tcpserver.server_close()
    udpserver.shutdown()
    udpserver.server_close()
    os._exit(0)

signal.signal(signal.SIGINT, sigint_handler)

class CDNHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = SimpleHTTPRequestHandler.translate_path(self, path)
        relpath = os.path.relpath(path, os.getcwd())
        fullpath = os.path.join(CDN_DIR, relpath)
        return fullpath

    def do_GET(self):
        # Check if client requested the map be overridden
        if self.path == '/gameassets/MapSchedule_v2.xml' and self.client_address[0] in map_override:
            self.send_response(200)
            self.send_header('Content-type', 'text/xml')
            self.end_headers()
            start = datetime.today() - timedelta(days=1)
            output = '<MapSchedule><appointments><appointment map="%s" start="%s"/></appointments><VERSION>1</VERSION></MapSchedule>' % (map_override[self.client_address[0]], start.strftime("%Y-%m-%dT00:01-04"))
            self.wfile.write(output.encode())
            del map_override[self.client_address[0]]
            return
        if self.path == '/gameassets/PortalRoadSchedule_v1.xml' and self.client_address[0] in climb_override:
            self.send_response(200)
            self.send_header('Content-type', 'text/xml')
            self.end_headers()
            start = datetime.today() - timedelta(days=1)
            output = '<PortalRoads><PortalRoadSchedule><appointments><appointment road="%s" portal="0" start="%s"/></appointments><VERSION>1</VERSION></PortalRoadSchedule></PortalRoads>' % (climb_override[self.client_address[0]], start.strftime("%Y-%m-%dT00:01-04"))
            self.wfile.write(output.encode())
            del climb_override[self.client_address[0]]
            return
        if CDN_PROXY and self.path.startswith('/gameassets/') and not self.path.endswith('_ver_cur.xml') and not ('User-Agent' in self.headers and 'python-urllib3' in self.headers['User-Agent']):
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(PoolManager().request('GET', 'http://cdn.zwift.com%s' % self.path).data)
                return
            except Exception as exc:
                print('Error trying to proxy: %s' % repr(exc))

        SimpleHTTPRequestHandler.do_GET(self)

class DeviceType:
    Relay = 1
    Zc = 2

class ChannelType:
    UdpClient = 1
    UdpServer = 2
    TcpClient = 3
    TcpServer = 4

class Packet:
    flags = None
    ri = None
    ci = None
    sn = None
    payload = None

class InitializationVector:
    def __init__(self, dt = 0, ct = 0, ci = 0, sn = 0):
        self._dt = struct.pack('!h', dt)
        self._ct = struct.pack('!h', ct)
        self._ci = struct.pack('!h', ci)
        self._sn = struct.pack('!i', sn)
    @property
    def dt(self):
        return self._dt
    @dt.setter
    def dt(self, v):
        self._dt = struct.pack('!h', v)
    @property
    def ct(self):
        return self._ct
    @ct.setter
    def ct(self, v):
        self._ct = struct.pack('!h', v)
    @property
    def ci(self):
        return self._ci
    @ci.setter
    def ci(self, v):
        self._ci = struct.pack('!h', v)
    @property
    def sn(self):
        return self._sn
    @sn.setter
    def sn(self, v):
        self._sn = struct.pack('!i', v)
    @property
    def data(self):
        return bytearray(2) + self._dt + self._ct + self._ci + self._sn

def decode_packet(data, key, iv):
    p = Packet()
    s = 1
    p.flags = data[0]
    if p.flags & 4:
        p.ri = int.from_bytes(data[s:s+4], "big")
        s += 4
    if p.flags & 2:
        p.ci = int.from_bytes(data[s:s+2], "big")
        iv.ci = p.ci
        s += 2
    if p.flags & 1:
        p.sn = int.from_bytes(data[s:s+4], "big")
        iv.sn = p.sn
        s += 4
    aesgcm = AES.new(key, AES.MODE_GCM, iv.data)
    p.payload = aesgcm.decrypt(data[s:])
    return p

def encode_packet(payload, key, iv, ri, ci, sn):
    flags = 0
    header = b''
    if ri is not None:
        flags = flags | 4
        header += struct.pack('!i', ri)
    if ci is not None:
        flags = flags | 2
        header += struct.pack('!h', ci)
    if sn is not None:
        flags = flags | 1
        header += struct.pack('!i', sn)
    aesgcm = AES.new(key, AES.MODE_GCM, iv.data)
    header = struct.pack('b', flags) + header
    aesgcm.update(header)
    ep, tag = aesgcm.encrypt_and_digest(payload)
    return header + ep + tag[:4]

class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        self.data = self.request.recv(1024)
        ip = self.client_address[0] + str(self.client_address[1])
        if not ip in global_clients.keys():
            relay_id = int.from_bytes(self.data[3:7], "big")
            ENCRYPTION_KEY_FILE = "%s/%s/encryption_key.bin" % (STORAGE_DIR, relay_id)
            if relay_id in global_relay.keys():
                with open(ENCRYPTION_KEY_FILE, 'wb') as f:
                    f.write(global_relay[relay_id].key)
            elif os.path.isfile(ENCRYPTION_KEY_FILE):
                with open(ENCRYPTION_KEY_FILE, 'rb') as f:
                    global_relay[relay_id] = zo.Relay(f.read())
            else:
                print('No encryption key for relay ID %s' % relay_id)
                return
            global_clients[ip] = global_relay[relay_id]
        if int.from_bytes(self.data[0:2], "big") != len(self.data) - 2:
            print("Wrong packet size")
            return
        relay = global_clients[ip]
        iv = InitializationVector(DeviceType.Relay, ChannelType.TcpClient, relay.tcp_ci, 0)
        p = decode_packet(self.data[2:], relay.key, iv)
        if p.ci is not None:
            relay.tcp_ci = p.ci
            relay.tcp_r_sn = 1
            relay.tcp_t_sn = 0
            iv.ci = p.ci
        if len(p.payload) > 1 and p.payload[1] != 0:
            print("TCPHandler hello(0) expected, got %s" % p.payload[1])
            return
        hello = udp_node_msgs_pb2.ClientToServer()
        try:
            hello.ParseFromString(p.payload[2:-8]) #2 bytes: payload length, 1 byte: =0x1 (TcpClient::sendClientToServer) 1 byte: type; payload; 4 bytes: hash
            #type: TcpClient::sayHello(=0x0), TcpClient::sendSubscribeToSegment(=0x1), TcpClient::processSegmentUnsubscription(=0x1)
        except Exception as exc:
            print('TCPHandler ParseFromString exception: %s' % repr(exc))
            return
        # send packet containing UDP server (127.0.0.1)
        msg = udp_node_msgs_pb2.ServerToClient()
        msg.player_id = hello.player_id
        msg.world_time = 0
        details1 = msg.udp_config.relay_addresses.add()
        details1.lb_realm = udp_node_msgs_pb2.ZofflineConstants.RealmID
        details1.lb_course = 6 # watopia crowd
        details1.ip = zo.server_ip
        details1.port = 3022
        details2 = msg.udp_config.relay_addresses.add()
        details2.lb_realm = 0 #generic load balancing realm
        details2.lb_course = 0 #generic load balancing course
        details2.ip = zo.server_ip
        details2.port = 3022
        msg.udp_config.uc_f2 = 10
        msg.udp_config.uc_f3 = 30
        msg.udp_config.uc_f4 = 3
        wdetails1 = msg.udp_config_vod_1.relay_addresses_vod.add()
        wdetails1.lb_realm = udp_node_msgs_pb2.ZofflineConstants.RealmID
        wdetails1.lb_course = 6 # watopia crowd
        wdetails1.relay_addresses.append(details1)
        wdetails2 = msg.udp_config_vod_1.relay_addresses_vod.add()
        wdetails2.lb_realm = 0  #generic load balancing realm
        wdetails2.lb_course = 0 #generic load balancing course
        wdetails2.relay_addresses.append(details2)
        msg.udp_config_vod_1.port = 3022
        payload = msg.SerializeToString()
        iv.ct = ChannelType.TcpServer
        r = encode_packet(payload, relay.key, iv, None, None, None)
        relay.tcp_t_sn += 1
        self.request.sendall(struct.pack('!h', len(r)) + r)

        player_id = hello.player_id
        self.request.settimeout(1) #make recv non-blocking
        while True:
            self.data = b''
            try:
                self.data = self.request.recv(1024)
                i = 0
                while i < len(self.data):
                    size = int.from_bytes(self.data[i:i+2], "big")
                    packet = self.data[i:i+size+2]
                    iv.ct = ChannelType.TcpClient
                    iv.sn = relay.tcp_r_sn
                    p = decode_packet(packet[2:], relay.key, iv)
                    relay.tcp_r_sn += 1
                    if len(p.payload) > 1 and p.payload[1] == 1:
                        subscr = udp_node_msgs_pb2.ClientToServer()
                        try:
                            subscr.ParseFromString(p.payload[2:-8])
                        except Exception as exc:
                            print('TCPHandler ParseFromString exception: %s' % repr(exc))
                        if subscr.subsSegments:
                            msg1 = udp_node_msgs_pb2.ServerToClient()
                            msg1.server_realm = udp_node_msgs_pb2.ZofflineConstants.RealmID
                            msg1.player_id = subscr.player_id
                            msg1.world_time = zo.world_time()
                            msg1.ackSubsSegm.extend(subscr.subsSegments)
                            payload1 = msg1.SerializeToString()
                            iv.ct = ChannelType.TcpServer
                            iv.sn = relay.tcp_t_sn
                            r = encode_packet(payload1, relay.key, iv, None, None, None)
                            relay.tcp_t_sn += 1
                            self.request.sendall(struct.pack('!h', len(r)) + r)
                    i += size + 2
            except:
                pass #timeout is ok here

            try:
                #if ZC need to be registered
                if player_id in zo.zc_connect_queue:
                    zc_params = udp_node_msgs_pb2.ServerToClient()
                    zc_params.player_id = player_id
                    zc_params.world_time = 0
                    zc_params.zc_local_ip = zo.zc_connect_queue[player_id][0]
                    zc_params.zc_local_port = zo.zc_connect_queue[player_id][1] #simple:21587, secure:21588
                    if zo.zc_connect_queue[player_id][2] != "None":
                        zc_params.zc_key = zo.zc_connect_queue[player_id][2]
                    zc_params.zc_protocol = udp_node_msgs_pb2.IPProtocol.TCP #=2
                    zc_params_payload = zc_params.SerializeToString()
                    iv.ct = ChannelType.TcpServer
                    iv.sn = relay.tcp_t_sn
                    r = encode_packet(zc_params_payload, relay.key, iv, None, None, None)
                    relay.tcp_t_sn += 1
                    self.request.sendall(struct.pack('!h', len(r)) + r)
                    zo.zc_connect_queue.pop(player_id)

                message = udp_node_msgs_pb2.ServerToClient()
                message.server_realm = udp_node_msgs_pb2.ZofflineConstants.RealmID
                message.player_id = player_id
                message.world_time = zo.world_time()

                #PlayerUpdate
                if player_id in player_update_queue and len(player_update_queue[player_id]) > 0:
                    added_player_updates = list()
                    for player_update_proto in player_update_queue[player_id]:
                        player_update = message.updates.add()
                        player_update.ParseFromString(player_update_proto)

                        #Send if 10 updates has already been added and start a new message
                        if len(message.updates) > 9:
                            message_payload = message.SerializeToString()
                            iv.ct = ChannelType.TcpServer
                            iv.sn = relay.tcp_t_sn
                            r = encode_packet(message_payload, relay.key, iv, None, None, None)
                            relay.tcp_t_sn += 1
                            self.request.sendall(struct.pack('!h', len(r)) + r)

                            message = udp_node_msgs_pb2.ServerToClient()
                            message.server_realm = udp_node_msgs_pb2.ZofflineConstants.RealmID
                            message.player_id = player_id
                            message.world_time = zo.world_time()

                        added_player_updates.append(player_update_proto)
                    for player_update_proto in added_player_updates:
                        player_update_queue[player_id].remove(player_update_proto)

                #Check if any updates are added and should be sent to client, otherwise just keep alive
                if len(message.updates) > 0:
                    message_payload = message.SerializeToString()
                    iv.ct = ChannelType.TcpServer
                    iv.sn = relay.tcp_t_sn
                    r = encode_packet(message_payload, relay.key, iv, None, None, None)
                    relay.tcp_t_sn += 1
                    self.request.sendall(struct.pack('!h', len(r)) + r)
                else:
                    iv.ct = ChannelType.TcpServer
                    iv.sn = relay.tcp_t_sn
                    r = encode_packet(payload, relay.key, iv, None, None, None)
                    relay.tcp_t_sn += 1
                    self.request.sendall(struct.pack('!h', len(r)) + r)
            except Exception as exc:
                print('TCPHandler loop exception: %s' % repr(exc))
                break

class BotVariables:
    profile = None
    route = None
    date = 0
    position = 0

class GhostsVariables:
    loaded = False
    started = False
    rec = None
    play = None
    last_rec = 0
    last_play = 0
    last_rt = 0
    start_road = 0
    start_rt = 0

def save_ghost(name, player_id):
    if not player_id in global_ghosts.keys(): return
    ghosts = global_ghosts[player_id]
    if len(ghosts.rec.states) > 0:
        state = ghosts.rec.states[0]
        folder = '%s/%s/ghosts/%s/' % (STORAGE_DIR, player_id, zo.get_course(state))
        if state.route: folder += str(state.route)
        else:
            folder += str(zo.road_id(state))
            if not zo.is_forward(state): folder += '/reverse'
        try:
            if not os.path.isdir(folder):
                os.makedirs(folder)
        except Exception as exc:
            print('save_ghost: %s' % repr(exc))
            return
        ghosts.rec.player_id = player_id
        f = '%s/%s-%s.bin' % (folder, datetime.now(timezone.utc).strftime("%Y-%m-%d-%H-%M-%S"), name)
        with open(f, 'wb') as fd:
            fd.write(ghosts.rec.SerializeToString())

def load_ghosts_folder(folder, ghosts):
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            if f.endswith('.bin'):
                with open(os.path.join(folder, f), 'rb') as fd:
                    g = BotVariables()
                    g.route = udp_node_msgs_pb2.Ghost()
                    g.route.ParseFromString(fd.read())
                    g.date = g.route.states[0].worldTime
                    ghosts.play.append(g)

def load_ghosts(player_id, state, ghosts):
    folder = '%s/%s/ghosts/%s' % (STORAGE_DIR, player_id, zo.get_course(state))
    road_folder = '%s/%s' % (folder, zo.road_id(state))
    if not zo.is_forward(state): road_folder += '/reverse'
    load_ghosts_folder(road_folder, ghosts)
    if state.route:
        load_ghosts_folder('%s/%s' % (folder, state.route), ghosts)
    ghosts.start_road = zo.road_id(state)
    ghosts.start_rt = state.roadTime
    with open('%s/start_lines.csv' % SCRIPT_DIR) as fd:
        sl = [tuple(line) for line in csv.reader(fd)]
        rt = [t for t in sl if t[0] == str(state.route)]
        if rt:
            ghosts.start_road = int(rt[0][1])
            ghosts.start_rt = int(rt[0][2])

def regroup_ghosts(player_id):
    p = online[player_id]
    ghosts = global_ghosts[player_id]
    if not ghosts.loaded:
        ghosts.loaded = True
        load_ghosts(player_id, p, ghosts)
    if not ghosts.started and ghosts.play:
        ghosts.started = True
    for g in ghosts.play:
        states = [(s.roadTime, s.distance) for s in g.route.states if zo.road_id(s) == zo.road_id(p) and zo.is_forward(s) == zo.is_forward(p)]
        if states:
            c = min(states, key=lambda x: sum(abs(r - d) for r, d in zip((p.roadTime, p.distance), x)))
            g.position = 0
            while g.route.states[g.position].roadTime != c[0] or g.route.states[g.position].distance != c[1]:
                g.position += 1
            if is_ahead(p, g.route.states[g.position].roadTime):
                g.position += 1
    ghosts.last_play = 0

def load_pace_partners():
    for (root, dirs, files) in os.walk(PACE_PARTNERS_DIR):
        for d in dirs:
            profile = os.path.join(PACE_PARTNERS_DIR, d, 'profile.bin')
            route = os.path.join(PACE_PARTNERS_DIR, d, 'route.bin')
            if os.path.isfile(profile) and os.path.isfile(route):
                with open(profile, 'rb') as fd:
                    p = profile_pb2.PlayerProfile()
                    p.ParseFromString(fd.read())
                    global_pace_partners[p.id] = BotVariables()
                    pp = global_pace_partners[p.id]
                    pp.profile = p
                with open(route, 'rb') as fd:
                    pp.route = udp_node_msgs_pb2.Ghost()
                    pp.route.ParseFromString(fd.read())
                    pp.position = 0

def play_pace_partners():
    while True:
        start = time.perf_counter()
        for pp_id in global_pace_partners.keys():
            pp = global_pace_partners[pp_id]
            if pp.position < len(pp.route.states) - 1: pp.position += 1
            else: pp.position = 0
            pp.route.states[pp.position].id = pp_id
        pause = pacer_update_freq - (time.perf_counter() - start)
        if pause > 0: time.sleep(pause)

def load_bots():
    body_types = [16, 48, 80, 272, 304, 336, 528, 560, 592]
    hair_types = [25953412, 175379869, 398510584, 659452569, 838618949, 924073005, 1022111028, 1262230565, 1305767757, 1569595897, 1626212425, 1985754517, 2234835005, 2507058825, 3092564365, 3200039653, 3296520581, 3351295312, 3536770137, 4021222889, 4179410997, 4294226781]
    facial_hair_types = [248681634, 398510584, 867351826, 1947387842, 2173853954, 3169994930, 4131541011, 4216468066]
    multiplier = 1
    with open(ENABLE_BOTS_FILE) as f:
        try:
            multiplier = int(f.readline().rstrip('\r\n'))
        except ValueError:
            pass
    bots_file = '%s/bot.txt' % STORAGE_DIR
    if not os.path.isfile(bots_file):
        bots_file = '%s/bot.txt' % SCRIPT_DIR
    with open(bots_file) as f:
        data = json.load(f)
    i = 1
    loop_riders = []
    for name in os.listdir(STORAGE_DIR):
        path = '%s/%s/ghosts' % (STORAGE_DIR, name)
        if os.path.isdir(path):
            for (root, dirs, files) in os.walk(path):
                for f in files:
                    if f.endswith('.bin'):
                        positions = []
                        for n in range(0, multiplier):
                            p = profile_pb2.PlayerProfile()
                            p.CopyFrom(zo.random_profile(p))
                            p.id = i + 1000000 + n * 10000
                            global_bots[p.id] = BotVariables()
                            bot = global_bots[p.id]
                            if n == 0:
                                bot.route = udp_node_msgs_pb2.Ghost()
                                with open(os.path.join(root, f), 'rb') as fd:
                                    bot.route.ParseFromString(fd.read())
                                positions = list(range(len(bot.route.states)))
                                random.shuffle(positions)
                            else:
                                bot.route = global_bots[i + 1000000].route
                            bot.position = positions.pop()
                            if not loop_riders:
                                loop_riders = data['riders'].copy()
                                random.shuffle(loop_riders)
                            rider = loop_riders.pop()
                            for item in ['first_name', 'last_name', 'is_male', 'country_code', 'ride_jersey', 'bike_frame', 'bike_wheel_front', 'bike_wheel_rear', 'ride_helmet_type', 'glasses_type', 'ride_shoes_type', 'ride_socks_type']:
                                if item in rider:
                                    setattr(p, item, rider[item])
                            p.body_type = random.choice(body_types)
                            p.hair_type = random.choice(hair_types)
                            if p.is_male:
                                p.facial_hair_type = random.choice(facial_hair_types)
                            else:
                                p.body_type += 1
                            bot.profile = p
                        i += 1

def play_bots():
    while True:
        start = time.perf_counter()
        if zo.reload_pacer_bots:
            zo.reload_pacer_bots = False
            if os.path.isfile(ENABLE_BOTS_FILE):
                global_bots.clear()
                load_bots()
        for bot_id in global_bots.keys():
            bot = global_bots[bot_id]
            if bot.position < len(bot.route.states) - 1: bot.position += 1
            else: bot.position = 0
            bot.route.states[bot.position].id = bot_id
        pause = bot_update_freq - (time.perf_counter() - start)
        if pause > 0: time.sleep(pause)

def remove_inactive():
    while True:
        for p_id in list(online.keys()):
            if zo.world_time() > online[p_id].worldTime + 10000:
                zo.logout_player(p_id)
        time.sleep(1)

def get_empty_message(player_id):
    message = udp_node_msgs_pb2.ServerToClient()
    message.server_realm = udp_node_msgs_pb2.ZofflineConstants.RealmID
    message.player_id = player_id
    message.seqno = 1
    message.stc_f5 = 1
    message.stc_f11 = 1
    message.msgnum = 1
    return message

def is_state_new_for(peer_player_state, player_id):
    if not player_id in global_news.keys():
        global_news[player_id] = {}
    for_news = global_news[player_id]
    if peer_player_state.id in for_news.keys():
        if for_news[peer_player_state.id] == peer_player_state.worldTime:
            return False #already sent
    for_news[peer_player_state.id] = peer_player_state.worldTime
    return True

def nearby_distance(s1, s2):
    if s1 is None or s2 is None:
        return False, None
    if zo.get_course(s1) == zo.get_course(s2):
        dist = math.sqrt((s2.x - s1.x)**2 + (s2.z - s1.z)**2 + (s2.y_altitude - s1.y_altitude)**2)
        if dist <= 100000 or zo.road_id(s1) == zo.road_id(s2):
            return True, dist
    return False, None

def is_ahead(state, roadTime):
    if zo.is_forward(state):
        if state.roadTime > roadTime and abs(state.roadTime - roadTime) < 500000:
            return True
    else:
        if state.roadTime < roadTime and abs(state.roadTime - roadTime) < 500000:
            return True
    return False

class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data = self.request[0]
        socket = self.request[1]
        ip = self.client_address[0] + str(self.client_address[1])
        if not ip in global_clients.keys():
            relay_id = int.from_bytes(data[1:5], "big")
            if relay_id in global_relay.keys():
                global_clients[ip] = global_relay[relay_id]
            else:
                return
        relay = global_clients[ip]
        iv = InitializationVector(DeviceType.Relay, ChannelType.UdpClient, relay.udp_ci, relay.udp_r_sn)
        p = decode_packet(data, relay.key, iv)
        relay.udp_r_sn += 1
        if p.ci is not None:
            relay.udp_ci = p.ci
            relay.udp_t_sn = 0
            iv.ci = p.ci
        if p.sn is not None:
            relay.udp_r_sn = p.sn

        recv = udp_node_msgs_pb2.ClientToServer()

        try:
            recv.ParseFromString(p.payload[:-8])
        except:
            try:
                #If no sensors connected, first byte must be skipped
                recv.ParseFromString(p.payload[1:-8])
            except Exception as exc:
                print('UDPHandler ParseFromString exception: %s' % repr(exc))
                return

        client_address = self.client_address
        player_id = recv.player_id
        state = recv.state

        global last_state
        global power
        run = math.sqrt((state.x - last_state.x)**2 + (state.z - last_state.z)**2)
        rise = state.y_altitude - last_state.y_altitude
        if run > 0:
            slope = round(rise * 100 / run)
            if slope >= 3:
                power = INCREASED_POWER
            elif slope <= -3:
                power = DECREASED_POWER
            else:
                power = DEFAULT_POWER
        last_state = state

        if power_meter:
            power_meter.update(power)

        #Add last updates for player if missing
        if not player_id in last_pp_updates.keys():
            last_pp_updates[player_id] = 0
        if not player_id in last_bot_updates.keys():
            last_bot_updates[player_id] = 0

        t = time.monotonic()

        #Update player online state
        if state.roadTime:
            if player_id in online.keys():
                if online[player_id].worldTime > state.worldTime:
                    return #udp is unordered -> drop old state
            else:
                discord.change_presence(len(online) + 1)
            online[player_id] = state

        #Add handling of ghosts for player if it's missing
        if not player_id in global_ghosts.keys():
            global_ghosts[player_id] = GhostsVariables()
            global_ghosts[player_id].rec = udp_node_msgs_pb2.Ghost()
            global_ghosts[player_id].play = []

        ghosts = global_ghosts[player_id]

        if player_id in ghosts_enabled and ghosts_enabled[player_id]:
            if state.roadTime and ghosts.last_rt and state.roadTime != ghosts.last_rt:
                #Load ghosts when start moving (as of version 1.39 player sometimes enters course 6 road 0 at home screen)
                if not ghosts.loaded:
                    ghosts.loaded = True
                    load_ghosts(player_id, state, ghosts)
                #Save player state as ghost
                if t >= ghosts.last_rec + bot_update_freq:
                    ghosts.rec.states.append(state)
                    ghosts.last_rec = t
                #Start loaded ghosts
                if not ghosts.started and ghosts.play and zo.road_id(state) == ghosts.start_road and is_ahead(state, ghosts.start_rt):
                    regroup_ghosts(player_id)
            ghosts.last_rt = state.roadTime

        #Set state of player being watched
        watching_state = None
        if state.watchingRiderId == player_id:
            watching_state = state
        elif state.watchingRiderId in online.keys():
            watching_state = online[state.watchingRiderId]
        elif state.watchingRiderId in global_pace_partners.keys():
            pp = global_pace_partners[state.watchingRiderId]
            watching_state = pp.route.states[pp.position]
        elif state.watchingRiderId in global_bots.keys():
            bot = global_bots[state.watchingRiderId]
            watching_state = bot.route.states[bot.position]
        elif state.watchingRiderId > 10000000:
            ghost = ghosts.play[math.floor(state.watchingRiderId / 10000000) - 1]
            if len(ghost.route.states) > ghost.position:
                watching_state = ghost.route.states[ghost.position]

        #Check if online players, pace partners, bots and ghosts are nearby
        nearby = {}
        for p_id in online.keys():
            player = online[p_id]
            if player.id != player_id:
                is_nearby, distance = nearby_distance(watching_state, player)
                if is_nearby and is_state_new_for(player, player_id):
                    nearby[p_id] = distance
        if t >= last_pp_updates[player_id] + pacer_update_freq:
            last_pp_updates[player_id] = t
            for p_id in global_pace_partners.keys():
                pp = global_pace_partners[p_id]
                is_nearby, distance = nearby_distance(watching_state, pp.route.states[pp.position])
                if is_nearby:
                    nearby[p_id] = distance
        if t >= last_bot_updates[player_id] + bot_update_freq:
            last_bot_updates[player_id] = t
            for p_id in global_bots.keys():
                bot = global_bots[p_id]
                is_nearby, distance = nearby_distance(watching_state, bot.route.states[bot.position])
                if is_nearby:
                    nearby[p_id] = distance
        if ghosts.started and t >= ghosts.last_play + bot_update_freq:
            ghosts.last_play = t
            for i, g in enumerate(ghosts.play):
                if len(g.route.states) > g.position:
                    is_nearby, distance = nearby_distance(watching_state, g.route.states[g.position])
                    if is_nearby:
                        nearby[player_id + (i + 1) * 10000000] = distance
                    g.position += 1

        #Send nearby riders states or empty message
        message = get_empty_message(player_id)
        if nearby:
            if len(nearby) > 100:
                nearby = dict(sorted(nearby.items(), key=lambda item: item[1]))
                nearby = dict(itertools.islice(nearby.items(), 100))
            message.num_msgs = math.ceil(len(nearby) / 10)
            for p_id in nearby:
                player = None
                if p_id in online.keys():
                    player = online[p_id]
                elif p_id in global_pace_partners.keys():
                    pp = global_pace_partners[p_id]
                    player = pp.route.states[pp.position]
                    player.worldTime = zo.world_time() - simulated_latency
                elif p_id in global_bots.keys():
                    bot = global_bots[p_id]
                    player = bot.route.states[bot.position]
                    player.worldTime = zo.world_time() - simulated_latency
                elif p_id > 10000000:
                    ghost = ghosts.play[math.floor(p_id / 10000000) - 1]
                    player = ghost.route.states[ghost.position - 1]
                    player.id = p_id
                    player.worldTime = zo.world_time() - simulated_latency
                if player != None:
                    if len(message.states) > 9:
                        message.world_time = zo.world_time()
                        message.cts_latency = message.world_time - recv.world_time
                        iv.ct = ChannelType.UdpServer
                        iv.sn = relay.udp_t_sn
                        r = encode_packet(message.SerializeToString(), relay.key, iv, None, None, relay.udp_t_sn)
                        relay.udp_t_sn += 1
                        socket.sendto(r, client_address)
                        message.msgnum += 1
                        del message.states[:]
                    message.states.append(player)
        else:
            message.num_msgs = 1
        message.world_time = zo.world_time()
        message.cts_latency = message.world_time - recv.world_time
        iv.ct = ChannelType.UdpServer
        iv.sn = relay.udp_t_sn
        r = encode_packet(message.SerializeToString(), relay.key, iv, None, None, relay.udp_t_sn)
        relay.udp_t_sn += 1
        socket.sendto(r, client_address)

if os.path.isdir(PACE_PARTNERS_DIR):
    load_pace_partners()
    pp = threading.Thread(target=play_pace_partners)
    pp.start()

if os.path.isfile(ENABLE_BOTS_FILE):
    load_bots()
    bot = threading.Thread(target=play_bots)
    bot.start()

socketserver.ThreadingTCPServer.allow_reuse_address = True
httpd = socketserver.ThreadingTCPServer(('', 80), CDNHandler)
zoffline_thread = threading.Thread(target=httpd.serve_forever)
zoffline_thread.daemon = True
zoffline_thread.start()

tcpserver = socketserver.ThreadingTCPServer(('', 3025), TCPHandler)
tcpserver_thread = threading.Thread(target=tcpserver.serve_forever)
tcpserver_thread.daemon = True
tcpserver_thread.start()

socketserver.ThreadingUDPServer.allow_reuse_address = True
udpserver = socketserver.ThreadingUDPServer(('', 3024), UDPHandler)
udpserver_thread = threading.Thread(target=udpserver.serve_forever)
udpserver_thread.daemon = True
udpserver_thread.start()

ri = threading.Thread(target=remove_inactive)
ri.start()

if os.path.exists(FAKE_DNS_FILE):
    from fake_dns import fake_dns
    dns = threading.Thread(target=fake_dns, args=(zo.server_ip,))
    dns.start()

try:
    devs = find(find_all=True, idVendor=0x0fcf)
    for dev in devs:
        if dev.idProduct in [0x1008, 0x1009]:
            stick = driver.USB2Driver(log=None, debug=False, idProduct=dev.idProduct, bus=dev.bus, address=dev.address)
            try:
                stick.open()
            except:
                continue
            stick.close()
            break
    else:
        print("No ANT devices available")
        sys.exit()

    antnode = node.Node(stick)
    print("Starting ANT node")
    antnode.start()
    key = node.Network(NETKEY, 'N:ANT+')
    antnode.setNetworkKey(0, key)

    print("Starting power meter with ANT+ ID " + repr(POWER_SENSOR_ID))
    try:
        # Create the power meter object and open it
        power_meter = PowerMeterTx(antnode, POWER_SENSOR_ID)
        power_meter.open()
    except Exception as e:
        print("power_meter error: " + repr(e))
        power_meter = None

except Exception as e:
    print("Exception: " + repr(e))

zo.run_standalone(online, global_relay, global_pace_partners, global_bots, global_ghosts, ghosts_enabled, save_ghost, regroup_ghosts, player_update_queue, map_override, climb_override, discord)
