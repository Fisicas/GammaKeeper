"""Rebuild versioned offline exports from immutable spectra and saved telemetry."""
import bootstrap
import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import numpy as np
from analysis import reconstruct, midpoint, VERSION, DOSE_R_TO_USV, timestamp
from storage import immutable_json


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    db.execute('PRAGMA query_only=ON')
    return db


def export_run(run, target=None, with_npz=True):
    run = Path(run).resolve()
    name = 'export_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    target = Path(target).resolve() if target else run / name
    target.mkdir(parents=True, exist_ok=False)
    config = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    # Consistent point-in-time copy even when recording is still active.
    source = readonly(run / 'run.sqlite')
    db = sqlite3.connect(target / 'source_index.sqlite')
    source.backup(db)
    source.close()
    check = db.execute('PRAGMA quick_check').fetchone()[0]
    if check != 'ok':
        raise ValueError(f'SQLite integrity check failed: {check}')
    fields = ['seq', 'start_utc', 'end_utc', 'epoch', 'quality', 'nominal_time_bin',
              'device_duration_s', 'wall_duration_s', 'total_counts', 'spectral_count_rate_cps',
              'poisson_sigma_cps', 'device_estimator_mean_cps', 'dose_rate_mean_uSv_h',
              'telemetry_coverage_fraction', 'integrated_dose_uSv', 'observed_integrated_dose_uSv',
              'cumulative_observed_integrated_dose_uSv', 'cumulative_dose_time_coverage', 'device_accumulated_dose_uSv',
              'device_experiment_dose_uSv', 'dose_reset_seen', 'device_dose_sample_utc', 'device_dose_age_s',
              'device_dose_timestamp_quality', 'host_utc_before', 'host_utc_after', 'clock_verified',
              'clock_offset_s', 'clock_estimated_uncertainty_s', 'device_estimator_sample_utc',
              'device_estimator_age_s', 'count_rate_err_percent_latest',
              'dose_rate_err_percent_latest', 'device_flags_latest', 'read_time_halfwidth_s',
              'raw_file', 'raw_sha256']
    previous = None
    epoch = 0
    chunk = []
    chunk_number = 0
    saved, normal, aggregates, boundaries = 0, 0, 0, 0
    normal_counts = 0
    recovered_counts = 0
    def flush_chunk():
        nonlocal chunk_number
        if not chunk or not with_npz:
            chunk.clear()
            return
        nchan = len(chunk[0][0]['spectrum']['counts'])
        if any(len(s['spectrum']['counts']) != nchan for s, _ in chunk):
            raise ValueError('Channel count changed inside an export chunk')
        counts = np.full((len(chunk), nchan), -1, dtype=np.int64)
        valid = np.zeros(len(chunk), dtype=bool)
        nominal = np.zeros(len(chunk), dtype=bool)
        for i, (s, d) in enumerate(chunk):
            if d['counts_delta'] is not None:
                counts[i] = d['counts_delta']
                valid[i] = True
                nominal[i] = d['quality'] == ['normal'] and not s.get('final_partial_interval')
        np.savez_compressed(target / f'spectra_{chunk_number:05d}.npz',
                            counts_delta=counts, valid=valid, nominal_time_bin=nominal,
                            channel=np.arange(nchan, dtype=np.int32),
                            calibration_keV=np.array([s['spectrum']['calibration_keV'] for s, _ in chunk]),
                            end_utc=np.array([dt.datetime.fromtimestamp(midpoint(s), dt.timezone.utc).isoformat() for s, _ in chunk]),
                            start_utc=np.array([dt.datetime.fromtimestamp(midpoint(s) - d['wall_duration_s'], dt.timezone.utc).isoformat() if d['wall_duration_s'] is not None else '' for s, d in chunk]),
                            read_utc_before=np.array([s['utc_before'] for s, _ in chunk]),
                            read_utc_after=np.array([s['utc_after'] for s, _ in chunk]),
                            seq=np.array([s['seq'] for s, _ in chunk], dtype=np.int64),
                            epoch=np.array([d['epoch'] for _, d in chunk], dtype=np.int64),
                            device_duration_s=np.array([d['device_duration_s'] if d['device_duration_s'] is not None else np.nan for _, d in chunk]),
                            quality=np.array([','.join(d['quality']) for _, d in chunk]))
        chunk_number += 1
        chunk.clear()
    try:
        with (target / 'time_bins.csv').open('x', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for seq, name, digest in db.execute('SELECT seq,file,sha256 FROM snapshots ORDER BY seq'):
                payload = (run / name).read_bytes()
                if hashlib.sha256(payload).hexdigest() != digest:
                    raise ValueError(f'Raw integrity mismatch {name}')
                s = json.loads(payload)
                if s['seq'] != saved:
                    raise ValueError('Missing/reordered raw snapshot')
                d = reconstruct(previous, s, config['interval_s'], epoch)
                epoch = d['epoch']
                tb = s.get('telemetry_bin', {})
                rare, rt = s.get('accumulated_dose') or {}, s.get('realtime') or {}
                is_normal = d['quality'] == ['normal'] and not s.get('final_partial_interval')
                aggregate = d['counts_delta'] is not None and not is_normal
                writer.writerow(dict(seq=seq, start_utc=dt.datetime.fromtimestamp(midpoint(previous), dt.timezone.utc).isoformat() if previous else '',
                    end_utc=dt.datetime.fromtimestamp(midpoint(s), dt.timezone.utc).isoformat(),
                    epoch=epoch, quality=','.join(d['quality']), nominal_time_bin=is_normal,
                    device_duration_s=d['device_duration_s'], wall_duration_s=d['wall_duration_s'], total_counts=d.get('total_counts'),
                    spectral_count_rate_cps=d['count_rate_cps'], poisson_sigma_cps=d['poisson_sigma_cps'],
                    device_estimator_mean_cps=tb.get('device_count_rate_cps'), dose_rate_mean_uSv_h=tb.get('dose_rate_uSv_h'),
                    telemetry_coverage_fraction=tb.get('coverage_fraction'), integrated_dose_uSv=tb.get('integrated_dose_uSv'),
                    observed_integrated_dose_uSv=tb.get('observed_integrated_dose_uSv'),
                    cumulative_observed_integrated_dose_uSv=s.get('derived_observed_dose_uSv'),
                    cumulative_dose_time_coverage=s.get('derived_observed_dose_coverage'),
                    device_accumulated_dose_uSv=rare.get('dose', 0) * DOSE_R_TO_USV if rare else '',
                    device_experiment_dose_uSv=s.get('device_experiment_dose_uSv'), dose_reset_seen=s.get('dose_reset_seen'),
                    device_dose_sample_utc=rare.get('dt', ''), device_dose_age_s=midpoint(s) - timestamp(rare['dt']) if rare else '',
                    device_dose_timestamp_quality=rare.get('timestamp_quality', 'device exchange timestamp'),
                    host_utc_before=s.get('host_utc_before'), host_utc_after=s.get('host_utc_after'),
                    clock_verified=(s.get('clock') or {}).get('verified'), clock_offset_s=(s.get('clock') or {}).get('offset_s'),
                    clock_estimated_uncertainty_s=(s.get('clock') or {}).get('estimated_uncertainty_s'),
                    device_estimator_sample_utc=rt.get('dt', ''), device_estimator_age_s=midpoint(s) - timestamp(rt['dt']) if rt else '',
                    count_rate_err_percent_latest=rt.get('count_rate_err'), dose_rate_err_percent_latest=rt.get('dose_rate_err'),
                    device_flags_latest=rt.get('flags'), read_time_halfwidth_s=s['read_time_halfwidth_s'], raw_file=name, raw_sha256=digest))
                if is_normal:
                    normal += 1
                    normal_counts += d['total_counts']
                elif aggregate:
                    aggregates += 1
                    recovered_counts += d['total_counts']
                else:
                    boundaries += 1
                if chunk and (len(chunk[0][0]['spectrum']['counts']) != len(s['spectrum']['counts'])):
                    flush_chunk()
                chunk.append((s, d))
                if len(chunk) >= 1000:
                    flush_chunk()
                previous = s
                saved += 1
            flush_chunk()
        telemetry_fields = ['batch_id', 'connection_id', 'received_utc', 'type', 'dt', 'count_rate', 'dose_rate',
                            'count_rate_err', 'dose_rate_err', 'count', 'duration', 'dose', 'temperature',
                            'charge_level', 'flags', 'real_time_flags', 'event', 'event_param1']
        nrecords = 0
        warnings = 0
        with (target / 'telemetry.csv').open('x', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=telemetry_fields, extrasaction='ignore')
            writer.writeheader()
            for bid, payload in db.execute('SELECT id,payload FROM telemetry ORDER BY id'):
                batch = json.loads(payload)
                warnings += bool(batch['result']['decode_warning'])
                for r in batch['result']['records']:
                    writer.writerow(dict(r, batch_id=bid, connection_id=batch['connection_id'], received_utc=batch['utc_after']))
                    nrecords += 1
        with (target / 'events.jsonl').open('x', encoding='utf-8') as file:
            for eid, utc, kind, payload in db.execute('SELECT id,utc,kind,payload FROM events ORDER BY id'):
                file.write(json.dumps(dict(id=eid, utc=utc, kind=kind, payload=json.loads(payload)), ensure_ascii=False) + '\n')
        report = dict(schema=1, software_version=VERSION, source_run=str(run), exported_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                      snapshots=saved, nominal_bins=normal, aggregate_intervals=aggregates, baseline_or_invalid_boundaries=boundaries,
                      nominal_counts=normal_counts, aggregate_counts=recovered_counts, decoded_telemetry_records=nrecords,
                      telemetry_decode_warnings=warnings, npz_chunks=chunk_number, sqlite_check=check,
                      manifest_sha256=hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest(),
                      count_rule='C(t2)-C(t1), divided by actual device elapsed seconds; first snapshot is baseline',
                      uncertainty='sqrt(N)/device duration is counting-only Poisson sigma; no calibration/clock/systematic uncertainty included',
                      dose_rule='Native telemetry.csv dose_rate is R/h and dose is R; time_bins.csv uses instrument convention 10000 uSv/R',
                      telemetry_rule='Time-weighted device estimators; <=3s zero-order hold; no extrapolation across long gaps',
                      npz_rule='Integer channel differences, -1 sentinel for invalid/baseline; consult valid, nominal_time_bin, quality, epoch; no resampling',
                      processing='No smoothing, background subtraction, normalization or calibration changes; UTC axes and ascending channel index')
        immutable_json(target / 'EXPORT_COMPLETE.json', report)
        return target, report
    finally:
        db.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--no-npz', action='store_true')
    args = parser.parse_args()
    folder, report = export_run(args.run, args.target, not args.no_npz)
    print(json.dumps(dict(folder=str(folder), **report), indent=2))
