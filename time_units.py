"""Strict human time expressions, returned as seconds without rounding."""
import math
import re

UNITS = {
    's': 1, 'sec': 1, 'secs': 1, 'second': 1, 'seconds': 1,
    'min': 60, 'mins': 60, 'minute': 60, 'minutes': 60, 'm': 60,
    'h': 3600, 'hr': 3600, 'hrs': 3600, 'hour': 3600, 'hours': 3600,
    'd': 86400, 'day': 86400, 'days': 86400,
    'w': 604800, 'week': 604800, 'weeks': 604800,
    'ms': .001, 'millisecond': .001, 'milliseconds': .001,
}
NUMBER = r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
TERM = re.compile(r'(' + NUMBER + r')\s*([a-z]+)', re.I)


def parse_time(value, default_unit='s', continuous=False):
    """Units may be compounded (1h 30min); bare numbers use the named default.

    Clock syntax is HH:MM:SS, never MM:SS. Negative and nonfinite values fail.
    """
    text = str(value).strip().lower()
    if continuous and text in ('continuous', 'unlimited'):
        return 0.
    if default_unit not in UNITS:
        raise ValueError('Unknown default time unit')
    if re.fullmatch(NUMBER, text):
        result = float(text) * UNITS[default_unit]
    elif ':' in text:
        parts = text.split(':')
        if len(parts) != 3 or not all(re.fullmatch(NUMBER, item) for item in parts):
            raise ValueError('Clock notation must be HH:MM:SS; otherwise include units')
        hours, minutes, seconds = map(float, parts)
        if hours != int(hours) or minutes != int(minutes) or minutes >= 60 or seconds >= 60:
            raise ValueError('Invalid HH:MM:SS expression')
        result = hours * 3600 + minutes * 60 + seconds
    else:
        position, result = 0, 0.
        terms = list(TERM.finditer(text))
        if not terms:
            raise ValueError('Enter a time such as 10 s, 60 minutes or 1 hour')
        for match in terms:
            if text[position:match.start()].strip() or match[2] not in UNITS:
                raise ValueError('Unknown time unit or invalid time expression')
            result += float(match[1]) * UNITS[match[2]]
            position = match.end()
        if text[position:].strip():
            raise ValueError('Unknown time unit or invalid time expression')
    if not math.isfinite(result) or result < 0:
        raise ValueError('Time must be finite and nonnegative')
    return result


def display_time(seconds):
    for suffix, scale in (('days', 86400), ('h', 3600), ('min', 60)):
        if seconds >= scale and math.isclose(seconds / scale, round(seconds / scale), abs_tol=1e-10):
            return f'{seconds / scale:.12g} {suffix}'
    return f'{seconds:.12g} s'
