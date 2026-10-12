import bootstrap
import json
import time
from device import ReadoutDevice
import os as _os
SERIAL = _os.environ.get('GAMMAKEEPER_SERIAL') or __import__('sys').exit('Set GAMMAKEEPER_SERIAL to the detector serial number')

with ReadoutDevice(SERIAL) as d:
    print(json.dumps(d.info(), indent=2), flush=True)
    print('SFR_DESCRIPTION', d.commands(), flush=True)
    for i in range(3):
        s = d.read_spectrum()
        print(json.dumps({'duration_s': s['duration_s'], 'channels': len(s['counts']), 'sum': sum(s['counts']),
                          'calibration_keV': s['calibration_keV']}), flush=True)
        print(json.dumps(d.read_buffer(), indent=2), flush=True)
        time.sleep(2)
