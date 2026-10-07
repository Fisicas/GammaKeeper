"""Pure reconstruction rules. Raw integer channel counts are never resampled."""
import datetime as dt
import math

VERSION = '0.3.1'
DOSE_R_TO_USV = 1e4  # Instrument convention: 1 R -> 0.01 Sv, not a universal physical conversion.


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include UTC offset')
    return parsed.timestamp()


def midpoint(snapshot):
    return (timestamp(snapshot['utc_before']) + timestamp(snapshot['utc_after'])) / 2


def validate_snapshot(snapshot):
    counts = snapshot['spectrum']['counts']
    if not counts or any(type(x) is not int or not 0 <= x <= 0xFFFFFFFF for x in counts):
        raise ValueError('Invalid raw spectrum channel counts')
    duration = snapshot['spectrum']['duration_s']
    calib = snapshot['spectrum']['calibration_keV']
    if not math.isfinite(duration) or duration < 0 or len(calib) != 3 or not all(math.isfinite(x) for x in calib):
        raise ValueError('Invalid spectrum metadata')
    before, after = timestamp(snapshot['utc_before']), timestamp(snapshot['utc_after'])
    if after < before:
        # A wall-clock step during the read is evidence, not grounds to discard counts.
        snapshot['clock_step_during_read'] = True
    return counts


def reconstruct(previous, current, interval_s, previous_epoch=0):
    counts = validate_snapshot(current)
    out = dict(epoch=previous_epoch, quality=[], counts_delta=None, count_rate_cps=None,
               poisson_sigma_cps=None, device_duration_s=None, wall_duration_s=None)
    if previous is None:
        out['quality'] = ['baseline']
        return out
    old_counts = validate_snapshot(previous)
    old_sp, sp = previous['spectrum'], current['spectrum']
    reasons = []
    if previous['serial'] != current['serial']:
        reasons.append('serial_changed')
    if len(counts) != len(old_counts):
        reasons.append('channel_count_changed')
    if old_sp['calibration_keV'] != sp['calibration_keV']:
        reasons.append('calibration_changed')
    if previous['spectrum_generation'] != current['spectrum_generation']:
        reasons.append('reset_or_power_event')
    seconds = sp['duration_s'] - old_sp['duration_s']
    if seconds <= 0:
        reasons.append('duration_not_increasing')
    if len(counts) == len(old_counts) and any(b < a for a, b in zip(old_counts, counts)):
        reasons.append('count_counter_decreased')
    out['wall_duration_s'] = midpoint(current) - midpoint(previous)
    out['device_duration_s'] = seconds
    if reasons:
        out['epoch'] += 1
        out['quality'] = reasons + ['new_baseline']
        return out
    delta = [b - a for a, b in zip(old_counts, counts)]
    n = sum(delta)  # Python integers; no unsigned underflow or clipping.
    out.update(counts_delta=delta, count_rate_cps=n / seconds,
               poisson_sigma_cps=math.sqrt(n) / seconds, total_counts=n)
    if current['connection_id'] != previous['connection_id']:
        out['quality'].append('reconnect_aggregate_continuity_inferred')
    if current.get('had_disconnect') or out['wall_duration_s'] > interval_s * 1.5:
        out['quality'].append('gap_recovered_aggregate')
    if current['boot_id'] == previous['boot_id']:
        m0 = (previous['monotonic_before'] + previous['monotonic_after']) / 2
        m1 = (current['monotonic_before'] + current['monotonic_after']) / 2
        if abs(out['wall_duration_s'] - (m1 - m0)) > 0.25:
            out['quality'].append('host_clock_step')
    if current.get('clock_step_during_read'):
        out['quality'].append('host_clock_step_during_read')
    if abs(seconds - out['wall_duration_s']) > max(2.5, out['wall_duration_s'] * .05):
        out['quality'].append('device_host_duration_disagree')
    if current.get('final_partial_interval'):
        out['quality'].append('final_short_interval')
    if not out['quality']:
        out['quality'] = ['normal']
    return out


def weighted_telemetry(records, start, end, max_hold_s=3):
    """Time-weighted device estimators, zero-order hold <=3s, no gap interpolation.

    Keep per-record errors and flags in the raw telemetry. Do not average those
    error percentages as if they were independent confidence intervals.
    """
    if end <= start:
        return dict(coverage_fraction=0, dose_rate_uSv_h=None, device_count_rate_cps=None,
                    integrated_dose_uSv=None)
    rows = sorted(((timestamp(r['dt']), r) for r in records if r['type'] == 'RealTimeData'), key=lambda item: item[0])
    dose, count, coverage = 0., 0., 0.
    for i, (t, r) in enumerate(rows):
        next_t = rows[i + 1][0] if i + 1 < len(rows) else end
        a, b = max(start, t), min(end, next_t, t + max_hold_s)
        if b <= a:
            continue
        width = b - a
        dose += width * r['dose_rate'] * DOSE_R_TO_USV
        count += width * r['count_rate']
        coverage += width
    fraction = min(1., coverage / (end - start))
    return dict(coverage_fraction=fraction, dose_rate_uSv_h=dose / coverage if coverage else None,
                device_count_rate_cps=count / coverage if coverage else None,
                integrated_dose_uSv=dose / 3600 if fraction >= .999 else None,
                observed_integrated_dose_uSv=dose / 3600, observed_duration_s=coverage,
                dose_convention='device R/h * 10000 -> uSv/h',
                estimator='zero-order hold; 3s maximum hold; means cover observed duration only')


def retry_delay(failure_index, initial=1., cap=60.):
    """First retry at 1s, then 2/4/8/16/32/60s. Cap exponent safely."""
    return min(cap, initial * 2 ** min(max(0, failure_index), 20))
