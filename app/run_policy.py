"""Read-only schedule preflight; no failed resume may alter a finished run."""
import datetime as dt
import json
import math
import time
import sys
import subprocess
from pathlib import Path
from export_run import readonly
from analysis import timestamp
from system_time import boot_identity


def resume_allowed(run, now=None):
    run = Path(run)
    if (run/'BACKUP_PLAN.json').exists():
        return False, 'This is a preserved backup snapshot. Start a new recording instead.'
    config = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
    if (run / 'run.sqlite').exists():
        db = readonly(run / 'run.sqlite')
        try:
            row = db.execute("SELECT value FROM state WHERE key='recorder'").fetchone()
            if row and json.loads(row[0]).get('status') == 'completed':
                return False, 'This recording completed successfully. Set up a new recording instead.'
        finally:
            db.close()
    end = config.get('planned_end_utc')
    if now is None and config.get('created_boot_id') is not None and config['created_boot_id']==boot_identity() and config.get('deadline_monotonic_s') is not None:
        expired=time.monotonic()>=config['deadline_monotonic_s']
    else:
        now = now if now is not None else dt.datetime.now(dt.timezone.utc).timestamp()
        expired=bool(end and timestamp(end)<=now)
    if expired:
        return False, 'The original scheduled end has passed. Set up a new recording from these settings.'
    return True, ''


def require_resume(run):
    config=json.loads((Path(run)/'manifest.json').read_text(encoding='utf-8'))
    now=None
    completed_allowed,completed_reason=resume_allowed(run,now=-float('inf'))
    if not completed_allowed:
        raise ValueError(completed_reason)
    if config.get('schedule_time_basis')=='sntp_reference' and (config.get('created_boot_id') is None or config.get('created_boot_id')!=boot_identity()):
        # After reboot the old monotonic deadline is invalid. Obtain an independent
        # bounded UTC reference before opening any writer or changing state.
        from runtime import python_command
        try:
            result=subprocess.run([python_command(),'-X','utf8',str(Path(__file__).with_name('clock_check.py'))],
                                  capture_output=True,timeout=8,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:raise ValueError('Clock query failed')
            measurement=json.loads(result.stdout)
        except (OSError,subprocess.SubprocessError,ValueError) as error:
            raise ValueError(f'UTC schedule verification unavailable; saved data are preserved: {error}') from error
        if not measurement.get('verified'):
            raise ValueError('UTC reference unavailable after reboot; schedule cannot be verified. Saved data are preserved.')
        from device import UTCClock
        now=UTCClock(measurement).seconds()
    allowed, reason = resume_allowed(run,now)
    if not allowed:
        raise ValueError(reason)


def validate_schedule(interval, seconds):
    if not math.isfinite(interval) or not 1 <= interval <= 86400:
        raise ValueError('Interval must be finite and within 1–86400 seconds')
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Duration must be finite and nonnegative')
