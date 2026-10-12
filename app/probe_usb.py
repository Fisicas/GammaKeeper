import bootstrap
import json
import struct
from phyton import device_paths, PhytonTransport
import os as _os
SERIAL = _os.environ.get('GAMMAKEEPER_SERIAL') or __import__('sys').exit('Set GAMMAKEEPER_SERIAL to the detector serial number')

print(json.dumps({'paths': device_paths()}, indent=2), flush=True)
with_transport = PhytonTransport(SERIAL)
try:
    # GET_VERSION only: no initialization, clock writes, or measurement resets.
    r = with_transport.execute(struct.pack('<I', 4) + struct.pack('<HBB', 0xA, 0, 0x80))
    print('GET_VERSION_RESPONSE', r.data().hex(), flush=True)
finally:
    with_transport.close()
