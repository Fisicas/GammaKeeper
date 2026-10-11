"""Validate v0.5.0 evidence and publish a versioned source-hash manifest."""
import bootstrap
from pathlib import Path
import datetime as dt
import hashlib
import json
import py_compile
import re
from recorder import versions

root = Path(__file__).resolve().parent
evidence = root.parent / 'time_units_export_20261011_v01'
logs = sorted((root / 'validation').glob('regression_tests_release_v*.txt'))
if not logs:
    raise AssertionError('Missing regression evidence')
log = logs[-1].read_text(encoding='utf-8-sig')
assert re.search(r'\nOK\s*$', log), 'Latest regression suite failed'
test_count = int(re.search(r'Ran (\d+) tests', log).group(1))
real = json.loads((evidence / 'saved_data_validation_v01.json').read_text())
assert real['all_channel_endpoint_conservation'] and real['original_manifest_and_database_unchanged'] and real['rcspg_roundtrips']
gui = json.loads(sorted(evidence.glob('gui_export_validation_v*.json'))[-1].read_text())
assert gui['detached_export_completed'] and gui['friendly_units_interpreted'] and gui['completion_visible']
smoke = json.loads((root / 'validation' / 'gui_smoke_report_v01.json').read_text())
assert all(smoke[key] for key in ('recording_survived_gui_close', 'stop_button_saved_cleanly', 'export_button_completed'))
originals = json.loads((evidence / 'source_v042_sha256.json').read_text())
assert all(hashlib.sha256((root.parent / 'radiacode_logger_v042' / name).read_bytes()).hexdigest() == digest for name, digest in originals.items())
sources = [*root.glob('*.py'), *(root / 'tests').glob('*.py')]
for file in sources:
    py_compile.compile(str(file), doraise=True)
files = sources + [*root.glob('*.md'), *root.glob('*.cmd'), root / 'requirements-app.txt', root / 'LICENSE']
result = dict(version='0.5.0', created_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
              automated_tests=test_count, tests_passed=True, regression_log=logs[-1].name,
              source_sha256={str(file.relative_to(root)): hashlib.sha256(file.read_bytes()).hexdigest() for file in files},
              versions=versions(), saved_run_validation=real, gui_validation=gui, gui_smoke=smoke,
              v042_original_source_unchanged=True, hardware_commands_sent=False, active_recorders_restarted=False,
              acquisition_changes='Flexible schedule parsing and upper interval limit; transport, clock, reconstruction and storage rules retained.',
              limitations=['Native Windows import of generated RCSPG and actual UFit execution untested.',
                           'One-second hardware acquisition unqualified; device exposure counter has one-second quantization.',
                           'Full week soak and automated Windows boot relaunch not established.'])
revision = 1
while (root / 'validation' / f'release_manifest_v{revision:02d}.json').exists():
    revision += 1
path = root / 'validation' / f'release_manifest_v{revision:02d}.json'
with path.open('x', encoding='utf-8') as handle:
    json.dump(result, handle, indent=2)
print(json.dumps(dict(manifest=str(path), tests=test_count, version=result['version']), indent=2))
