"""Independent background completion audit/export; never alters capture status."""
import bootstrap
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import numpy as np
from export_run import export_run, readonly
from storage import immutable_json
from analysis import VERSION, reconstruct
from run_quality import assess
from backup_run import digest as file_digest


def seal(run, target):
    run, target = Path(run).resolve(), Path(target).resolve()
    target.mkdir(parents=True, exist_ok=False)
    try:
        db = readonly(run/'run.sqlite')
        try:
            state = db.execute("SELECT value FROM state WHERE key='recorder'").fetchone()
            if not state or json.loads(state[0]).get('status') != 'completed':
                raise ValueError('Only a completed run can be sealed; capture status is preserved')
        finally:
            db.close()
        export, result = export_run(run, target/'export')
        config = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        db = readonly(export/'source_index.sqlite')
        try:
            integrity = [x[0] for x in db.execute('PRAGMA integrity_check')]
            if integrity != ['ok']:
                raise ValueError(f'Journal integrity: {integrity}')
            records = iter(db.execute('SELECT seq,file,sha256,derived FROM snapshots ORDER BY seq'))
            previous = None
            previous_epoch = 0
            counts = 0
            missing_seconds = 0.
            rows = 0
            quality = Counter()
            for file in sorted(export.glob('spectra_*.npz')):
                with np.load(file,allow_pickle=False) as data:
                    for i, seq in enumerate(data['seq']):
                        expected, raw_file, digest, derived = next(records)
                        if int(seq) != expected or expected != rows:
                            raise ValueError('Export sequence mismatch')
                        payload = (run/raw_file).read_bytes()
                        if hashlib.sha256(payload).hexdigest() != digest:
                            raise ValueError('Raw spectrum integrity mismatch during sealing')
                        current = json.loads(payload)
                        d = reconstruct(previous,current,config['interval_s'],previous_epoch)
                        if d['counts_delta']!=json.loads(derived).get('counts_delta'):
                            raise ValueError('Journal derived counts disagree with raw reconstruction')
                        previous_epoch=d['epoch']
                        quality.update(d['quality'])
                        if data['valid'][i]:
                            delta = (np.asarray(current['spectrum']['counts'],dtype=np.int64) -
                                     np.asarray(previous['spectrum']['counts'],dtype=np.int64))
                            if not np.array_equal(delta,data['counts_delta'][i]):
                                raise ValueError('Export channel differences disagree with raw counts')
                            counts += int(delta.sum())
                        previous = current
                        rows += 1
            if next(records,None) is not None or rows != result['snapshots']:
                raise ValueError('Export row count mismatch')
            events = dict(db.execute('SELECT kind,count(*) FROM events GROUP BY kind'))
            telemetry_hashes_checked=0
            if db.execute("SELECT count(*) FROM sqlite_master WHERE name='telemetry_integrity'").fetchone()[0]:
                for bid,payload,digest in db.execute('SELECT t.id,t.payload,i.sha256 FROM telemetry t LEFT JOIN telemetry_integrity i ON t.id=i.id'):
                    if digest:
                        if hashlib.sha256(payload.encode('utf-8')).hexdigest()!=digest:
                            raise ValueError(f'Raw telemetry payload hash mismatch: batch {bid}')
                        telemetry_hashes_checked+=1
        finally:
            db.close()
        import csv
        with (export/'time_bins.csv').open(encoding='utf-8',newline='') as handle:
            config = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
            coverage = 0.
            last = {}
            def rows_for_assessment():
                nonlocal last, coverage
                for row in csv.DictReader(handle):
                    last = row
                    coverage = float(row.get('cumulative_dose_time_coverage') or 0.)
                    yield row
            assessment = assess(config, rows_for_assessment(), quality, events, 0.)
            assessment['telemetry'] = dict(dose_time_coverage=coverage, status='covered' if coverage>=.999 else 'partial')
        device_elapsed = assessment['spectra']['device_exposure_s']
        hashes = {file.relative_to(target).as_posix():file_digest(file)
                  for file in target.rglob('*') if file.is_file()
                  and not file.name.endswith(('-wal','-shm'))}
        verdict = dict(schema=1,software_version=VERSION,passed=True,source_run=str(run),
                       source_manifest_sha256=result['manifest_sha256'], checked_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                       acquisition_state='completed', snapshots=rows, total_counts=counts,
                       mean_spectral_rate_cps=counts/device_elapsed if device_elapsed else None,
                       dose_uSv=float(last.get('cumulative_observed_integrated_dose_uSv') or 0),
                       dose_coverage=float(last.get('cumulative_dose_time_coverage') or 0),
                       telemetry_payload_hashes_checked=telemetry_hashes_checked,
                       data_quality=assessment['data_quality'], assessment=assessment,
                       quality_counts=dict(quality),events=events,sqlite_integrity=integrity,
                       export=str(export),file_hashes=hashes,
                       processing=result['processing'],dose_rule=result['telemetry_rule'],
                       qualification='File/reconstruction validation; not independent absolute dose, energy, sample timing or USB electrical calibration')
        immutable_json(target/'VALIDATION.json',verdict)
        return verdict
    except Exception as error:
        verdict = dict(schema=1,passed=False,source_run=str(run),acquisition_state='preserved',
                       checked_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                       error=f'{type(error).__name__}: {error}')
        immutable_json(target/'VALIDATION.json',verdict)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('run',type=Path)
    parser.add_argument('--target',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(seal(args.run,args.target),indent=2))
