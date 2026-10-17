"""Display-only scale helpers; no log transform or pseudocount in saved data."""
import numpy as np
from matplotlib.colors import LogNorm, Normalize


def display_values(values, logarithmic=False):
    result = np.ma.masked_invalid(np.ma.asarray(values, dtype=float))
    return np.ma.masked_less_equal(result, 0) if logarithmic else result


def apply_scale(axis, which, scale, values=()):
    if scale not in ('linear', 'log') or which not in ('x', 'y'):
        raise ValueError('Invalid display axis/scale')
    setter = axis.set_xscale if which == 'x' else axis.set_yscale
    limits = axis.set_xlim if which == 'x' else axis.set_ylim
    if scale == 'linear':
        setter('linear')
        return
    positive = display_values(values, True).compressed()
    # Supply a valid domain before enabling log, including baseline/all-zero
    # views. The fallback is a display extent, not an invented measurement.
    if positive.size:
        low, high = float(positive.min()), float(positive.max())
        if which == 'x':
            low, high = low / 1.05, high * 1.05
        else:
            low, high = low / 1.25, high * 1.25
        if low == high:
            low, high = low / 2, high * 2
    else:
        low, high = (1., 10.) if which == 'x' else (.1, 1.)
    limits(low, high)
    setter('log', nonpositive='mask')


def intensity_norm(values, scale, percentile=100.0):
    if scale not in ('linear', 'log'):
        raise ValueError('Invalid spectrogram scale')
    result = display_values(values, scale == 'log')
    positive = display_values(values, True).compressed()
    maximum = float(np.percentile(positive, percentile)) if positive.size else 1.
    if scale == 'log':
        minimum = float(positive.min()) if positive.size else .001
        maximum = max(maximum, minimum * 10 if maximum <= minimum else maximum)
        return result, LogNorm(vmin=minimum, vmax=maximum)
    return result, Normalize(vmin=0., vmax=max(maximum, np.finfo(float).tiny))
