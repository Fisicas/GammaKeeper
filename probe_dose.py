import bootstrap
import json
from device import ReadoutDevice
import struct
from radiacode.types import VSFR, COMMAND
with ReadoutDevice('RC-103-006489') as device:
    for register in [VSFR.DS_uR, VSFR.DR_uR_h, VSFR.CPS]:
        response = device.execute(COMMAND.RD_VIRT_SFR, struct.pack('<I', int(register)))
        print(json.dumps(dict(register=register.name, raw_hex=response.data().hex())), flush=True)
