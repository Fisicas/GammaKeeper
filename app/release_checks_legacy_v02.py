"""Compile current source and validate saved regression/live-test evidence."""
import bootstrap
import datetime as dt
import hashlib
import json
from pathlib import Path
import py_compile
import re
from recorder import versions
from storage import immutable_json
from audit_live import audit

root=Path(__file__).resolve().parent
sources=list(root.glob('*.py'))+list((root/'tests').glob('*.py'))
for file in sources:
    py_compile.compile(str(file),doraise=True)
log=(root/'validation'/'regression_tests_release_v01.txt').read_text(encoding='utf-8-sig')
if not re.search(r'\nOK\s*$',log):
    raise AssertionError('Release regression suite did not pass')
tests=int(re.search(r'Ran (\d+) tests',log).group(1))
pilots=[]
for name in ['live_fault_pilot_v01','live_fault_pilot_v02']:
    p=audit(root/'validation'/name)
    assert p['count_conservation'] and p['calibration_unchanged'] and not p['decoder_warnings']
    saved=json.loads((root/'validation'/f'{name}_report.json').read_text())
    assert saved['final_state']['supervisor']['restarts']>=1
    assert p['event_counts'].get('status_cache_unavailable')
    pilots.append(dict(name=name,snapshots=p['snapshot_count'],counts=p['added_counts'],conserved=True,
                       automatic_restart=True,cache_failure_nonfatal=True,calibration_unchanged=True))
smoke=json.loads((root/'validation'/'gui_smoke_report_v02.json').read_text())
assert all(smoke[key] for key in ['recording_survived_gui_close','stop_button_saved_cleanly','export_button_completed'])
old_hashes=json.loads((root.parent/'investigation_20261007_v01'/'v01_source_hashes.json').read_text())
assert all(hashlib.sha256((root.parent/'radiacode_logger_v01'/name).read_bytes()).hexdigest()==digest for name,digest in old_hashes.items())
original=root.parent/'radiacode_logger_v01'/'runs'/'20261006_233228_177627_RC-103-006489'
raw_hashes=json.loads((root.parent/'investigation_20261007_v01'/'raw_snapshot_hashes_v01.json').read_text())
assert all(hashlib.sha256((original/value['file']).read_bytes()).hexdigest()==value['sha256'] for value in raw_hashes)
files=sources+list(root.glob('*.md'))+list(root.glob('*.cmd'))+[root/'LICENSE',root/'requirements-app.txt']
hashes={str(file.relative_to(root)):hashlib.sha256(file.read_bytes()).hexdigest() for file in files}
revision=1
while (root/'validation'/f'release_validation_v{revision:02d}.json').exists():
    revision+=1
report=dict(version='0.2.0',created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),versions=versions(),
            automated_tests=tests,tests_passed=True,compiled_sources=len(sources),live_fault_pilots=pilots,
            gui_integration=smoke,original_655_raw_snapshots_unchanged=True,v01_source_unchanged=True,
            source_sha256=hashes,qualification='Windows locking and crash/restart regressions plus short live fault pilots passed. A full overnight/24h soak remains a separate qualification step.')
immutable_json(root/'validation'/f'release_validation_v{revision:02d}.json',report)
print(json.dumps({k:v for k,v in report.items() if k not in ('source_sha256','versions')},indent=2))
