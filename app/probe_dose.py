import bootstrap
import json
from device import ReadoutDevice
import struct
from radiacode.types import VSFR, COMMAND
import os as _os
SERIAL = _os.environ.get('GAMMAKEEPER_SERIAL') or __import__('sys').exit('Set GAMMAKEEPER_SERIAL to the detector serial number')
with ReadoutDevice(SERIAL) as device:
    for register in [VSFR.DS_uR, VSFR.DR_uR_h, VSFR.CPS]:
        response = device.execute(COMMAND.RD_VIRT_SFR, struct.pack('<I', int(register)))
        print(json.dumps(dict(register=register.name, raw_hex=response.data().hex())), flush=True)
