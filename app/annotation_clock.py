"""Clock evidence evaluated at annotation entry, without altering snapshot metadata."""
import time
from system_time import boot_identity


def evidence(saved):
    clock=dict(saved or {})
    if clock.get('anchor_monotonic_s') is None or clock.get('system_boot_id')!=boot_identity():
        return dict(quality='unverified',verified=False,scope='Host entry clock; previous recorder reference is unavailable on this boot')
    good=clock.get('last_good_monotonic_s')
    age=time.perf_counter()-good if good is not None else None
    elapsed=max(0.,time.perf_counter()-clock['anchor_monotonic_s'])
    pending=clock.get('slew_total_s',0.)
    residual=pending-max(-elapsed*.005,min(pending,elapsed*.005))
    quality='unverified' if age is None else 'holdover' if age>1800 or clock.get('last_check_failure') else 'referenced'
    if quality=='referenced':quality='slewing' if abs(residual)>.001 else 'single_source' if clock.get('source_count',2)<2 else 'referenced'
    base=clock.get('base_uncertainty_s')
    clock.update(quality=quality,verified=age is not None and quality!='holdover',reference_age_s=age,
                 slew_remaining_s=residual,estimated_uncertainty_s=base+abs(residual)+age*100e-6 if base is not None and age is not None else None,
                 scope='Entry/click reference; does not calibrate a manually entered historical event time')
    return clock
