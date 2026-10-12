"""Independent coverage assessment; file integrity does not prove experimental completeness."""
from analysis import timestamp


def assess(config, bins, quality, events, dose_coverage):
    first = last = None
    valid_wall = device = normal_device = aggregate_device = invalid_wall = 0.
    intervals = aggregates = 0
    timing_flags = {'host_clock_step', 'host_clock_step_during_read', 'device_host_duration_disagree'}
    for row in bins:
        end = timestamp(row['end_utc'])
        first = end if first is None else first
        last = end
        flags = set(row['quality'].split(','))
        wall = max(0., float(row.get('wall_duration_s') or 0.))
        if row.get('total_counts') not in ('', None):
            exposure = max(0., float(row['device_duration_s']))
            device += exposure
            valid_wall += wall
            intervals += 1
            if flags & {'gap_recovered_aggregate', 'reconnect_aggregate_continuity_inferred'}:
                aggregates += 1
                aggregate_device += exposure
            else:
                normal_device += exposure
        elif 'baseline' not in flags:
            invalid_wall += wall
    span = max(0., last - first) if first is not None else 0.
    tolerance = max(2.5, config['interval_s'])
    scheduled = bool(config.get('planned_end_utc'))
    missing_start = missing_end = 0.
    fraction = None
    if scheduled:
        begin, end = timestamp(config['created_utc']), timestamp(config['planned_end_utc'])
        missing_start = max(0., (first if first is not None else end) - begin)
        missing_end = max(0., end - (last if last is not None else begin))
        fraction = max(0., min(1., (min(end, last) - max(begin, first)) / (end-begin))) if first is not None and end>begin else 0.
    warnings = []
    if not intervals: warnings.append('no_reconstructable_intervals')
    if quality.get('new_baseline', 0): warnings.append('spectrum_boundary_requires_review')
    if any(quality.get(flag, 0) for flag in timing_flags): warnings.append('timing_requires_review')
    if events.get('final_read_failed', 0): warnings.append('final_read_failed')
    if missing_start > tolerance: warnings.append('missing_scheduled_start')
    if missing_end > tolerance: warnings.append('missing_scheduled_tail')
    return dict(file_integrity='passed', data_quality='review_required' if warnings else
                'complete_with_aggregates' if aggregates else 'complete', warnings=warnings,
                schedule=dict(status='review_required' if missing_start>tolerance or missing_end>tolerance else
                              'covered_within_tolerance' if scheduled else 'unscheduled',
                              endpoint_coverage_fraction=fraction, missing_start_s=missing_start,
                              missing_tail_s=missing_end, tolerance_s=tolerance),
                spectra=dict(device_exposure_s=device, normal_device_exposure_s=normal_device,
                             aggregate_device_exposure_s=aggregate_device, aggregate_intervals=aggregates,
                             valid_interval_wall_s=valid_wall, invalid_interval_wall_s=invalid_wall,
                             endpoint_span_s=span, reconstructed_wall_fraction=min(1.,valid_wall/span) if span else 0.,
                             time_resolution='variable_width_aggregates' if aggregates else 'native_intervals'),
                telemetry=dict(dose_time_coverage=dose_coverage,
                               status='covered' if dose_coverage>=.999 else 'partial'),
                scientific_qc='not_evaluated',
                exposure_note='Device-reported duration; not independently calibrated live time or dead-time correction')
