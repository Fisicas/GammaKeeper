"""Source-aligned time-resolved spectra; no synthetic subintervals or resampling."""
import bootstrap
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import uuid
import numpy as np
from analysis import reconstruct, midpoint, timestamp, VERSION
from export_run import readonly
from time_units import parse_time


def iso(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()


def aligned_count(seconds, cadence, name):
    if seconds is None or not math.isfinite(seconds):
        raise ValueError(f'{name} requires a finite duration')
    ratio = seconds / cadence
    if seconds < cadence or not math.isclose(ratio, round(ratio), rel_tol=0, abs_tol=1e-8):
        raise ValueError(f'{name} must be a whole multiple of the saved {cadence:g} s cadence; finer intervals cannot be reconstructed')
    return int(round(ratio))


def group_indices(length, mode='native', width=1, step=1, number=1):
    """Return half-open source ranges, retaining final short nonoverlap bins."""
    if min(width, step, number) < 1:
        raise ValueError('Bin sizes and number must be positive')
    if mode == 'native':
        return [(i, i + 1) for i in range(length)]
    if mode == 'number':
        if number > length:
            raise ValueError(f'Requested {number} spectra but only {length} saved intervals in this segment')
        return [(i * length // number, (i + 1) * length // number) for i in range(number)]
    if mode == 'width':
        return [(i, min(i + width, length)) for i in range(0, length, width)]
    if mode == 'sliding':
        if step > width:
            raise ValueError('Sliding step cannot exceed averaging width; this would omit observations')
        if width > length:
            raise ValueError('Averaging window exceeds the available contiguous segment')
        return [(i, i + width) for i in range(0, length - width + 1, step)]
    raise ValueError('Unknown time bin mode')


def rcspg_payload(rows, counts, calibration, config, title):
    """Native JSON schema verified against existing Windows-app .rcspg files.

    Native timestamps are milliseconds, collectTime is device elapsed seconds.
    Extended precision, quality and selection provenance live in the sidecar.
    """
    if not rows or any(r['device_exposure_s'] <= 0 for r in rows):
        raise ValueError('RCSPG requires positive measured exposure')
    return dict(channelCount=counts.shape[1], id=str(uuid.uuid4()).upper(),
                deviceModel=config.get('device_model', config.get('serial', '').rsplit('-', 1)[0].replace('RC-', 'RadiaCode-')),
                title=title, startTimeTimestamp=round(rows[0]['start_epoch_s'] * 1000),
                spectrums=[dict(timestamp=round(r['end_epoch_s'] * 1000), collectTime=r['device_exposure_s'],
                                pulses=c.tolist()) for r, c in zip(rows, counts)],
                coefficients=list(calibration), deviceId=config['serial'])


def read_rcspg(path):
    """Validate native structure and restore omitted trailing zero channels."""
    value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    channels = value['channelCount']
    if type(channels) is not int or channels < 1 or len(value['coefficients']) != 3:
        raise ValueError('Invalid RCSPG dimensions/calibration')
    previous = None
    counts = []
    for row in value['spectrums']:
        if not math.isfinite(row['collectTime']) or row['collectTime'] <= 0:
            raise ValueError('Invalid RCSPG exposure')
        if previous is not None and row['timestamp'] <= previous:
            raise ValueError('RCSPG timestamps must increase')
        previous = row['timestamp']
        pulses = row['pulses']
        if len(pulses) > channels or any(type(p) is not int or p < 0 for p in pulses):
            raise ValueError('Invalid RCSPG counts')
        counts.append(pulses + [0] * (channels - len(pulses)))
    if not counts:
        raise ValueError('Empty RCSPG')
    return value, np.asarray(counts, dtype=np.int64)


def load_segments(run, start=None, end=None, epoch=None):
    """Capture an indexed horizon then hash-check immutable raw spectra only.

    Reconnect aggregates and suspect timing remain in exclusions, never spread
    into artificial regular bins. Every included row is a complete interval.
    """
    run = Path(run).resolve()
    manifest = (run / 'manifest.json').read_bytes()
    config = json.loads(manifest)
    if start is not None and end is not None and start >= end:
        raise ValueError('UTC start must precede end')
    db = readonly(run / 'run.sqlite')
    try:
        entries = list(db.execute('SELECT seq,file,sha256,epoch FROM snapshots ORDER BY seq'))
    finally:
        db.close()
    segments, excluded, sources = [], [], []
    previous = None
    previous_epoch = 0
    segment = []
    def flush():
        nonlocal segment
        if segment:
            segments.append(segment)
        segment = []
    for expected, (seq, name, digest, stored_epoch) in enumerate(entries):
        if seq != expected:
            raise ValueError('Missing/reordered raw snapshot sequence')
        path = (run / name).resolve()
        if not path.is_relative_to(run):
            raise ValueError('Raw path escapes source folder')
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError(f'Raw SHA-256 mismatch: {name}')
        current = json.loads(payload)
        if current['seq'] != seq:
            raise ValueError('Raw sequence differs from index')
        d = reconstruct(previous, current, config['interval_s'], previous_epoch)
        if d['epoch'] != stored_epoch:
            raise ValueError('Raw reconstruction epoch differs from index')
        begin = midpoint(previous) if previous else None
        finish = midpoint(current)
        sources.append(dict(seq=seq, file=name, sha256=digest, epoch=stored_epoch))
        selected = epoch is None or epoch == stored_epoch
        overlaps = selected and (end is None or begin is None or begin < end) and (start is None or finish > start)
        inside = selected and begin is not None and (start is None or begin >= start) and (end is None or finish <= end)
        safe = d['counts_delta'] is not None and d['device_duration_s'] > 0 and finish > begin and all(
            flag in ('normal', 'final_short_interval') for flag in d['quality'])
        if inside and safe:
            row = dict(seq=seq, epoch=stored_epoch, start_epoch_s=begin, end_epoch_s=finish,
                       device_exposure_s=d['device_duration_s'], quality=d['quality'],
                       calibration=current['spectrum']['calibration_keV'],
                       counts=np.asarray(d['counts_delta'], dtype=np.int64),
                       clock=current.get('clock', {}),
                       read_halfwidth_s=abs(finish - timestamp(current['utc_before'])))
            if segment and (row['epoch'] != segment[-1]['epoch'] or row['calibration'] != segment[-1]['calibration']):
                flush()
            segment.append(row)
        else:
            flush()
            if overlaps:
                excluded.append(dict(seq=seq, epoch=stored_epoch, start_utc=iso(begin) if begin is not None else None,
                                     end_utc=iso(finish), device_exposure_s=d['device_duration_s'],
                                     reason='incomplete_range_edge' if not inside and d['counts_delta'] is not None else ','.join(d['quality']),
                                     counts_delta=d['counts_delta']))
        previous, previous_epoch = current, d['epoch']
        if seq % 2000 == 0:
            print(f'Verified source spectrum {seq} / {len(entries)-1}', flush=True)
    flush()
    return config, segments, excluded, dict(manifest_sha256=hashlib.sha256(manifest).hexdigest(),
                                            source_head=entries[-1][0] if entries else -1, snapshots=sources)


def bin_segment(segment, mode, width, step, number, time_zero):
    rows, counts = [], []
    cumulative = None
    if mode == 'sliding' and step < width:
        cumulative = np.concatenate((np.zeros((1, len(segment[0]['counts'])), dtype=np.int64),
                                     np.cumsum(np.stack([row['counts'] for row in segment]), axis=0, dtype=np.int64)))
    for begin, end in group_indices(len(segment), mode, width, step, number):
        group = segment[begin:end]
        summed = cumulative[end]-cumulative[begin] if cumulative is not None else np.sum([row['counts'] for row in group], axis=0, dtype=np.int64)
        exposure = sum(row['device_exposure_s'] for row in group)
        a, b = group[0]['start_epoch_s'], group[-1]['end_epoch_s']
        rows.append(dict(first_seq=group[0]['seq'], last_seq=group[-1]['seq'], epoch=group[0]['epoch'],
                         source_intervals=len(group), start_epoch_s=a, end_epoch_s=b,
                         start_utc=iso(a), end_utc=iso(b), time_s=(a+b)/2-time_zero,
                         bin_start_s=a-time_zero, bin_end_s=b-time_zero, wall_duration_s=b-a,
                         device_exposure_s=exposure, total_counts=int(summed.sum()),
                         quality=sorted(set(flag for row in group for flag in row['quality'])),
                         final_short_group=(mode == 'width' and len(group) < width),
                         clock_quality=sorted(set(str(row['clock'].get('quality', 'not_recorded')) for row in group)),
                         clock_estimated_uncertainty_max_s=max((row['clock'].get('estimated_uncertainty_s', 0.) for row in group), default=0.),
                         read_halfwidth_max_s=max(row['read_halfwidth_s'] for row in group)))
        counts.append(summed)
    return rows, np.asarray(counts, dtype=np.int64)


def export_series(run, target=None, mode='native', width_s=None, step_s=None, number=1,
                  start=None, end=None, epoch=None, time_zero=None, xlsx=False, energy_min=None, energy_max=None,
                  keep_aggregates=False, time_zero_basis='auto', chunk_size=256):
    from series_stream import export_stream
    return export_stream(run, target, mode, width_s, step_s, number, start, end, epoch, time_zero,
                         xlsx, energy_min, energy_max, keep_aggregates, time_zero_basis, chunk_size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--target', type=Path)
    parser.add_argument('--mode', choices=('native', 'width', 'number', 'sliding'), default='native')
    parser.add_argument('--width', type=parse_time)
    parser.add_argument('--step', type=parse_time)
    parser.add_argument('--number', type=int, default=1)
    parser.add_argument('--start-utc', type=float)
    parser.add_argument('--end-utc', type=float)
    parser.add_argument('--epoch', type=int)
    parser.add_argument('--time-zero-utc', type=float)
    parser.add_argument('--xlsx', action='store_true')
    parser.add_argument('--keep-aggregates', action='store_true')
    parser.add_argument('--time-zero-basis', choices=('auto','seal','acquisition','custom'), default='auto')
    parser.add_argument('--chunk-size', type=int, default=256)
    parser.add_argument('--energy-min-kev', type=float)
    parser.add_argument('--energy-max-kev', type=float)
    args = parser.parse_args()
    if args.mode in ('width', 'sliding') and args.width is None:
        parser.error('--width is required')
    if args.mode == 'sliding' and args.step is None:
        parser.error('--step is required')
    export_series(args.run, args.target, args.mode, args.width, args.step, args.number, args.start_utc,
                  args.end_utc, args.epoch, args.time_zero_utc, args.xlsx, args.energy_min_kev, args.energy_max_kev,
                  args.keep_aggregates, args.time_zero_basis, args.chunk_size)


if __name__ == '__main__':
    main()
