"""Reproducible short-pilot audit; does not alter the saved run."""
import bootstrap
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from export_run import readonly
from analysis import reconstruct
from storage import immutable_json


def audit(run):
    run = Path(run).resolve()
    config = json.loads((run / 'manifest.json').read_text())
    db = readonly(run / 'run.sqlite')
    previous = None
    first = None
    epochs = 0
    delta_total = [0] * 1024
    intervals = []
    warnings = Counter()
    telemetry_types = Counter()
    dose_observations = []
    for seq, name, digest in db.execute('SELECT seq,file,sha256 FROM snapshots ORDER BY seq'):
        payload = (run / name).read_bytes()
        assert hashlib.sha256(payload).hexdigest() == digest
        s = json.loads(payload)
        d = reconstruct(previous, s, config['interval_s'], epochs)
        epochs = d['epoch']
        if first is None:
            first = s
        if d['counts_delta'] is not None:
            delta_total = [a + b for a, b in zip(delta_total, d['counts_delta'])]
        intervals.append(dict(seq=seq, quality=d['quality'], duration=d['device_duration_s'],
                              total_counts=d.get('total_counts'), rate_cps=d['count_rate_cps'],
                              dose_coverage=s.get('telemetry_bin', {}).get('coverage_fraction'),
                              experiment_dose_uSv=s.get('device_experiment_dose_uSv'),
                              clock=s.get('clock')))
        previous = s
    expected = [b - a for a, b in zip(first['spectrum']['counts'], previous['spectrum']['counts'])]
    assert delta_total == expected, 'Counts did not conserve across snapshots'
    assert all(a >= 0 for a in expected)
    for payload, in db.execute('SELECT payload FROM telemetry'):
        value = json.loads(payload)
        if value['result']['decode_warning']:
            warnings[value['result']['decode_warning']] += 1
        for r in value['result']['records']:
            telemetry_types[r['type']] += 1
            if r['type'] == 'RareData':
                dose_observations.append(dict(received_utc=value['utc_after'], **r))
    event_counts = dict(db.execute('SELECT kind,count(*) FROM events GROUP BY kind'))
    check = db.execute('PRAGMA quick_check').fetchone()[0]
    db.close()
    return dict(source_run=str(run), integrity_check=check, snapshot_count=len(intervals),
                channels=len(first['spectrum']['counts']), first_device_duration_s=first['spectrum']['duration_s'],
                last_device_duration_s=previous['spectrum']['duration_s'], added_counts=sum(expected),
                count_conservation=True, calibration_unchanged=first['spectrum']['calibration_keV'] == previous['spectrum']['calibration_keV'],
                event_counts=event_counts, telemetry_types=dict(telemetry_types), decoder_warnings=dict(warnings),
                intervals=intervals, device_dose_observations=dose_observations,
                qualification='Short connected-device pilot with software transport reopen; not a physical cable-removal or seven-day soak test')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = audit(args.run)
    if args.output:
        immutable_json(args.output, result)
    print(json.dumps(result, indent=2))
