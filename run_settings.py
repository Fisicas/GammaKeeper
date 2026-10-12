"""Read saved run identity and schedule without changing the manifest."""
import datetime as dt
import json
import math
from pathlib import Path


def load_run_settings(run):
    settings = json.loads((Path(run) / 'manifest.json').read_text(encoding='utf-8'))
    for key in ('label', 'serial', 'mode'):
        if not isinstance(settings[key], str):
            raise ValueError(f'Invalid saved {key}')
    interval = float(settings['interval_s'])
    duration = float(settings['duration_s'])
    if not math.isfinite(interval) or not 1 <= interval <= 86400:
        raise ValueError('Invalid saved spectrum interval')
    if not math.isfinite(duration) or duration < 0:
        raise ValueError('Invalid saved duration')
    if settings['mode'] not in ('device', 'demo'):
        raise ValueError('Invalid saved recording mode')
    for key in ('created_utc', 'planned_end_utc'):
        if key == 'planned_end_utc' and settings.get(key) is None:
            continue
        parsed = dt.datetime.fromisoformat(settings[key])
        if parsed.tzinfo is None:
            raise ValueError(f'Saved {key} must include a UTC offset')
    return settings


def format_duration(seconds):
    """Show the recorded duration, retaining fractional seconds when present."""
    days, remaining = divmod(float(seconds), 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, remaining = divmod(remaining, 60)
    parts = [f'{int(value)} {unit}' for value, unit in
             ((days, 'd'), (hours, 'h'), (minutes, 'min')) if value]
    if remaining or not parts:
        parts.append(f'{remaining:.9g} s')
    return ' '.join(parts)


def run_summary(settings, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    created = dt.datetime.fromisoformat(settings['created_utc'])
    end = dt.datetime.fromisoformat(settings['planned_end_utc']) if settings.get('planned_end_utc') else None
    duration = 'Continuous' if settings['duration_s'] == 0 else format_duration(settings['duration_s'])
    identity = (f"Run: {settings['label'] or '(unlabeled)'} • "
                f"Spectrum / count interval: {settings['interval_s']:g} s • Duration: {duration}")
    start_text = created.astimezone().strftime('%b %d, %H:%M:%S %z')
    if end:
        end_text = end.astimezone().strftime('%b %d, %H:%M:%S %z')
        remaining = (end - now).total_seconds()
        remaining_text = format_duration(math.ceil(remaining)) if remaining > 0 else 'Scheduled end reached'
        schedule = f'Start: {start_text} • Planned end: {end_text} • Time to planned end: {remaining_text}'
    else:
        end_text = 'None (continuous)' if settings['duration_s'] == 0 else 'Not recorded'
        schedule = f'Start: {start_text} • Planned end: {end_text}'
    return identity + '\n' + schedule
