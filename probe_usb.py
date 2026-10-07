import bootstrap
import json
import struct
from phyton import device_paths, PhytonTransport

print(json.dumps({'paths': device_paths()}, indent=2), flush=True)
with_transport = PhytonTransport('RC-103-006489')
try:
    # GET_VERSION only: no initialization, clock writes, or measurement resets.
    r = with_transport.execute(struct.pack('<I', 4) + struct.pack('<HBB', 0xA, 0, 0x80))
    print('GET_VERSION_RESPONSE', r.data().hex(), flush=True)
finally:
    with_transport.close()
