import os
import xml.etree.ElementTree as ET
import re
import csv

courses = {
    'Richmond': 2,
    'Watopia': 6,
    'London': 7,
    'New York': 8,
    'Innsbruck': 9,
    'Bologna': 10,
    'Yorkshire': 11,
    'Crit City': 12,
    'Makuri Islands': 13,
    'France': 14,
    'Paris': 15,
    'Gravel Mountain': 16,
    'Scotland': 17
}

data = []

for directory in os.listdir('.'):
    if not os.path.isdir(directory):
        continue

    course = courses[directory]

    for file in os.listdir(directory):
        if not file.endswith('.xml'):
            continue

        with open(os.path.join(directory, file)) as f:
            xml = f.read()

        tree = ET.fromstring(re.sub(r"(<\?xml[^>]+\?>)", r"\1<root>", xml) + "</root>")
        route = tree.find('route')
        name = route.get('name').strip()
        nameHash = route.get('nameHash')

        time = []

        private_spawn_area = tree.find('private_spawn_area')
        if private_spawn_area is not None:
            road = private_spawn_area.get('road')
            forward = private_spawn_area.get('forward')
            starttime = private_spawn_area.get('starttime')
            time.append(int(float(starttime)*1000000)+5000)
            endtime = private_spawn_area.get('endtime')
            time.append(int(float(endtime)*1000000)+5000)

        spawn_area = tree.find('spawn_area')
        if spawn_area is not None:
            road = spawn_area.get('road')
            forward = spawn_area.get('forward')
            starttime = spawn_area.get('starttime')
            time.append(int(float(starttime)*1000000)+5000)
            endtime = spawn_area.get('endtime')
            time.append(int(float(endtime)*1000000)+5000)

        highrescheckpoint = tree.find('highrescheckpoint')
        for entry in highrescheckpoint.iter('entry'):
            start_time = int(float(entry.get('time'))*1000000+5000)
            start_road = entry.get('road')
            break

        if time:
            data.append([course, road, forward, min(time), max(time), start_road, start_time, nameHash, name])

with open('start_lines.csv', 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['course', 'spawnRoad', 'isForward', 'spawnStart', 'spawnEnd', 'startRoad', 'startTime', 'nameHash', 'name'])
    writer.writerows(sorted(data, key=lambda row: (row[0], row[1], row[8])))
