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
from analysis import reconstruct, midpoint, timestamp
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
                  start=None, end=None, epoch=None, time_zero=None, xlsx=False, energy_min=None, energy_max=None):
    config, segments, excluded, source = load_segments(run, start, end, epoch)
    if not segments:
        raise ValueError('No complete intervals with valid timing in the selected range')
    if mode not in ('native', 'width', 'number', 'sliding') or type(number) is not int or number < 1:
        raise ValueError('Invalid export mode or number of spectra')
    if any(v is not None and not math.isfinite(v) for v in (energy_min, energy_max)):
        raise ValueError('Energy bounds must be finite')
    if energy_min is not None and energy_max is not None and energy_min >= energy_max:
        raise ValueError('Energy start must precede end')
    cadence = config['interval_s']
    width = aligned_count(width_s, cadence, 'Bin/window width') if mode in ('width', 'sliding') else 1
    step = aligned_count(step_s, cadence, 'Sliding step') if mode == 'sliding' else width
    if mode == 'number' and len(segments) != 1:
        raise ValueError('Number-of-spectra mode requires one contiguous epoch/segment; select a UTC range or epoch')
    if mode == 'number' and number > len(segments[0]):
        raise ValueError('Requested more spectra than saved intervals')
    if xlsx:
        import openpyxl  # Fail before creating an output folder if unavailable.
    if mode == 'sliding':
        # Do not silently skip short segments or manufacture partial windows.
        for segment in segments:
            group_indices(len(segment), mode, width, step, number)
    zero = time_zero if time_zero is not None else segments[0][0]['start_epoch_s']
    if not math.isfinite(zero):
        raise ValueError('Invalid time zero')
    target = Path(target).resolve() if target else Path(run) / ('time_resolved_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
    target.mkdir(parents=True, exist_ok=False)
    overlapping = mode == 'sliding' and step < width
    report = dict(schema='radialog-time-resolved-v1', software_version='0.5.0', source_run=str(Path(run).resolve()),
                  source_manifest=config, source=source, mode=mode, requested_width_s=width_s, requested_step_s=step_s,
                  requested_number=number if mode == 'number' else None, start_utc=iso(start) if start is not None else None,
                  end_utc=iso(end) if end is not None else None, selected_epoch=epoch, time_zero_utc=iso(zero),
                  source_cadence_s=cadence, overlapping=overlapping, excluded_intervals=len(excluded), segments=[],
                  processing='Integer sums of complete saved intervals; no smoothing, interpolation, spectral resampling or synthetic finer bins.',
                  timing='Host UTC read midpoints delimit intervals. time_s is the bin wall-time midpoint relative to time_zero_utc. Device exposure is separately preserved.',
                  uncertainty='sqrt(counts)/device_exposure_s: counting-only Poisson estimate; not a confidence interval and omits clock, calibration, dead-time and other systematic errors.',
                  fitting_notes=['Fit bin-integrated model predictions for rapid kinetics; midpoint exports alone do not correct finite-window averaging.',
                                 'Radon-chain ingrowth and decay may require a coupled chain model; no decay-associated spectra have been fitted here.'],
                  warnings=['Overlapping windows share photons and have correlated counting errors; use nonoverlapping spectra for ordinary independent-error fitting.'] if overlapping else [])
    with (target / 'excluded_intervals.jsonl').open('x', encoding='utf-8') as file:
        for row in excluded:
            file.write(json.dumps(row) + '\n')
    for index, segment in enumerate(segments):
        rows, counts = bin_segment(segment, mode, width, step, number, zero)
        channels = np.arange(counts.shape[1])
        a0, a1, a2 = segment[0]['calibration']
        energy = a0 + a1 * channels + a2 * channels ** 2
        if np.any(np.diff(energy) <= 0):
            raise ValueError('Calibration is not monotonically increasing; no axis reorder was applied')
        select = np.ones(len(energy), dtype=bool)
        if energy_min is not None:
            select &= energy >= energy_min
        if energy_max is not None:
            select &= energy <= energy_max
        if not select.any():
            raise ValueError('Energy selection includes no channels')
        exposure = np.asarray([row['device_exposure_s'] for row in rows])
        rates = counts / exposure[:, None]
        sigma = np.sqrt(counts) / exposure[:, None]
        stem = f'segment_{index:03d}_epoch_{segment[0]["epoch"]}'
        np.savez_compressed(target / (stem + '.npz'), counts=counts, rate_cps=rates, poisson_sigma_cps=sigma,
                            energy_keV=energy, channel=channels, calibration_keV=np.array(segment[0]['calibration']),
                            time_s=np.array([r['time_s'] for r in rows]), bin_start_s=np.array([r['bin_start_s'] for r in rows]),
                            bin_end_s=np.array([r['bin_end_s'] for r in rows]), device_exposure_s=exposure,
                            first_seq=np.array([r['first_seq'] for r in rows]), last_seq=np.array([r['last_seq'] for r in rows]))
        with (target / (stem + '_bins.jsonl')).open('x', encoding='utf-8') as file:
            for row in rows:
                file.write(json.dumps(row) + '\n')
        # Explicit eV headers match UFit's energy-axis convention. NPZ retains keV.
        header = ['Time (s)'] + [f'{v*1000:.12g} eV' for v in energy[select]]
        with (target / (stem + '_rate_cps.csv')).open('x', newline='', encoding='utf-8') as file:
            writer = csv.writer(file)
            writer.writerow(header)
            for row, rate in zip(rows, rates[:, select]):
                writer.writerow([row['time_s'], *rate])
        if xlsx:
            from openpyxl import Workbook
            book = Workbook(write_only=True)
            sheet = book.create_sheet('Spectral rate cps')
            sheet.append(header)
            for row, rate in zip(rows, rates[:, select]):
                sheet.append([row['time_s'], *map(float, rate)])
            book.save(target / (stem + '_UFit_rate_cps.xlsx'))
        rcspg = None
        if not overlapping:
            rcspg = stem + '.rcspg'
            payload = rcspg_payload(rows, counts, segment[0]['calibration'], config, config['label'] or 'GammaKeeper time-resolved spectra')
            (target / rcspg).write_text(json.dumps(payload, separators=(',', ':')), encoding='utf-8')
            _, restored = read_rcspg(target / rcspg)
            if not np.array_equal(restored, counts):
                raise ValueError('RCSPG count roundtrip failed')
        union = set()
        for r in rows:
            union.update(range(r['first_seq'], r['last_seq'] + 1))
        report['segments'].append(dict(stem=stem, epoch=segment[0]['epoch'], bins=len(rows), source_intervals=len(segment),
                                       intervals_used=len(union), unused_sliding_tail_intervals=len(segment)-len(union),
                                       calibration_keV=segment[0]['calibration'], rcspg=rcspg,
                                       matrix_channel_selection=channels[select].tolist(),
                                       output_sum_counts=int(counts.sum()),
                                       unique_source_counts=int(sum(int(row['counts'].sum()) for row in segment if row['seq'] in union)),
                                       start_utc=rows[0]['start_utc'], end_utc=rows[-1]['end_utc']))
        print(f'Exported {stem}: {len(rows)} spectra', flush=True)
    report['files'] = {file.name: hashlib.sha256(file.read_bytes()).hexdigest() for file in target.iterdir() if file.is_file()}
    (target / 'COMPLETE.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Complete: {target}', flush=True)
    return target, report


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
    parser.add_argument('--energy-min-kev', type=float)
    parser.add_argument('--energy-max-kev', type=float)
    args = parser.parse_args()
    if args.mode in ('width', 'sliding') and args.width is None:
        parser.error('--width is required')
    if args.mode == 'sliding' and args.step is None:
        parser.error('--step is required')
    export_series(args.run, args.target, args.mode, args.width, args.step, args.number, args.start_utc,
                  args.end_utc, args.epoch, args.time_zero_utc, args.xlsx, args.energy_min_kev, args.energy_max_kev)


if __name__ == '__main__':
    main()
