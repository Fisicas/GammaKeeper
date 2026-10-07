"""Final source compilation, automated test and saved pilot audit."""
import bootstrap
import datetime as dt
import hashlib
import importlib.metadata
import json
from pathlib import Path
import py_compile
import re
import subprocess
import sys

from audit_live import audit
from recorder import versions
from storage import immutable_json

root = Path(__file__).resolve().parent
sources = list(root.glob('*.py')) + list((root / 'tests').glob('*.py'))
for source in sources:
    py_compile.compile(str(source), doraise=True)
result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover', '-s', str(root / 'tests'), '-v'],
                        cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=60)
revision = 1
while (root / 'validation' / f'validation_report_v{revision:02d}.json').exists():
    revision += 1
test_log = root / 'validation' / f'automated_tests_final_v{revision:02d}.txt'
with test_log.open('x', encoding='utf-8') as file:
    file.write(result.stdout + result.stderr)
if result.returncode:
    print(result.stdout + result.stderr)
    raise SystemExit(result.returncode)
pilots = [audit(root / 'validation' / name) for name in ['live_pilot_v01', 'live_pilot_v02', 'live_pilot_v03']]
for pilot in pilots:
    assert pilot['count_conservation'] and pilot['calibration_unchanged']
    assert pilot['integrity_check'] == 'ok' and not pilot['decoder_warnings']
smoke = json.loads((root / 'validation' / 'gui_smoke_report_v01.json').read_text())
assert smoke['recording_survived_gui_close'] and smoke['stop_button_saved_cleanly'] and smoke['export_button_completed']
manifest_files = sources + list(root.glob('*.cmd')) + list(root.glob('*.md')) + list((root / 'THIRD_PARTY_LICENSES').glob('*.txt')) + [root / 'LICENSE', root / 'requirements-app.txt']
hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(manifest_files)}
local_versions = sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions(path=[str(root / 'dependencies')]))
if not (root / 'dependencies-lock-v01.txt').exists():
    with (root / 'dependencies-lock-v01.txt').open('x', encoding='utf-8') as file:
        file.write('\n'.join(local_versions) + '\n')
test_count = int(re.search(r'Ran (\d+) tests', result.stdout + result.stderr).group(1))
report = dict(created_utc=dt.datetime.now(dt.timezone.utc).isoformat(), versions=versions(),
              compiled_sources=len(sources), automated_tests=test_count, automated_tests_passed=True,
              gui_integration=smoke, live_pilots=pilots, source_sha256=hashes,
              qualification='Short live pilots and simulated numerical/GUI/fault checks passed. Seven-day live soak and physical USB unplug/replug remain unperformed.',
              upstream_protocol=dict(project='cdump/radiacode', version='0.4.0', reviewed_commit='2b217916f49f5eedddf8f0d9116fcfd3c6b8832e'),
              preservation='Device spectrum/dose/calibration reset writes blocked; original workspace spectra and vendor installation preserved')
immutable_json(root / 'validation' / f'validation_report_v{revision:02d}.json', report)
print(json.dumps(dict(automated_tests=test_count, gui_checks='passed', compiled_sources=len(sources),
                     pilots=[dict(snapshots=p['snapshot_count'], counts=p['added_counts'], conserved=p['count_conservation']) for p in pilots]), indent=2))
