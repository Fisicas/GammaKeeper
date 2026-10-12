"""Versioned evidence for display changes, preserving acquisition and raw data."""
import bootstrap
from pathlib import Path
import datetime as dt
import hashlib
import json
import py_compile
import re
import PySide6,numpy,matplotlib

root=Path(__file__).resolve().parent
evidence=root.parent/'display_scales_20261011_v01'
log=sorted((root/'validation').glob('regression_tests_release_v*.txt'))[-1]
text=log.read_text(encoding='utf-8-sig')
assert re.search(r'\nOK\s*$',text),'Latest regression suite must pass'
count=int(re.search(r'Ran (\d+) tests',text).group(1))
render=json.loads((evidence/'saved_run_render_validation_v01.json').read_text())
assert render['all_four_axis_combinations_rendered'] and render['source_row_unchanged'] and render['original_manifest_and_database_unchanged']
originals=json.loads((evidence/'source_v05_sha256.json').read_text())
old=root.parent/'radiacode_logger_v05'
assert all(hashlib.sha256((old/name).read_bytes()).hexdigest()==digest for name,digest in originals.items()),'Previous source was changed'
unchanged=('recorder.py','supervisor.py','device.py','worker_client.py','storage.py','spectrum_view.py','time_resolved.py','export_run.py')
assert all((root/name).read_bytes()==(old/name).read_bytes() for name in unchanged),'Acquisition/reconstruction/export code changed'
assert (root/'analysis.py').read_text().replace("VERSION = '0.5.1'","VERSION = '0.5.0'")==(old/'analysis.py').read_text()
files=[*root.glob('*.py'),*(root/'tests').glob('*.py')]
for file in files:py_compile.compile(str(file),doraise=True)
files += [*root.glob('*.md'),*root.glob('*.cmd'),root/'requirements-app.txt',root/'LICENSE']
result=dict(version='0.5.1',created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),tests=count,tests_passed=True,
            regression_log=log.name,saved_data_render=render,previous_source_unchanged=True,
            acquisition_and_export_code_unchanged=True,raw_data_modified=False,hardware_commands_sent=False,
            versions=dict(PySide6=PySide6.__version__,numpy=numpy.__version__,matplotlib=matplotlib.__version__),
            source_sha256={str(file.relative_to(root)):hashlib.sha256(file.read_bytes()).hexdigest() for file in files})
revision=1
while (root/'validation'/f'release_manifest_v{revision:02d}.json').exists():revision+=1
file=root/'validation'/f'release_manifest_v{revision:02d}.json'
with file.open('x',encoding='utf-8') as handle:json.dump(result,handle,indent=2)
print(json.dumps(dict(manifest=str(file),tests=count,version='0.5.1'),indent=2))
