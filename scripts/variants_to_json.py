import json
import protobuf.variants_pb2 as variants_pb2
from google.protobuf.json_format import MessageToJson

variants = variants_pb2.FeatureResponse()

with open("variant", "rb") as f:
    variants.ParseFromString(f.read())

with open('variants.txt', 'w') as f:
    f.write(MessageToJson(variants))
