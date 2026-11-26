"""Read-only schedule preflight; no failed resume may alter a finished run."""
import datetime as dt
import json
import math
import time
import subprocess
from pathlib import Path
from export_run import readonly
from analysis import retry_delay, timestamp
from system_time import boot_identity


class ClockReferenceUnavailable(ValueError):
    """A new verified clock sample could not yet be obtained."""


class ResumeCancelled(Exception):
    """The recorder received a stop request while waiting to resume."""


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


def require_resume(run, *, clock_timeout=8, require_verified=False):
    """Check eligibility, returning the new SNTP sample when the schedule needs one."""
    config = json.loads((Path(run) / 'manifest.json').read_text(encoding='utf-8'))
    now = measurement = None
    completed_allowed, completed_reason = resume_allowed(run, now=-float('inf'))
    if not completed_allowed:
        raise ValueError(completed_reason)
    created_boot = config.get('created_boot_id')
    same_boot = created_boot is not None and created_boot == boot_identity()
    needs_reference = config.get('schedule_time_basis') == 'sntp_reference' or (
        require_verified and config.get('mode') == 'device')
    if config.get('planned_end_utc') and needs_reference and not same_boot:
        # After reboot the old monotonic deadline is invalid. Obtain an independent
        # bounded UTC reference before opening any writer or changing state.
        from device import UTCClock
        from runtime import python_command
        try:
            result = subprocess.run([python_command(), '-X', 'utf8', str(Path(__file__).with_name('clock_check.py'))],
                                    capture_output=True, timeout=clock_timeout, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise ValueError('Clock query failed')
            measurement = json.loads(result.stdout)
            if not isinstance(measurement, dict) or measurement.get('verified') is not True:
                raise ValueError('UTC reference unavailable after reboot')
            if not all(math.isfinite(measurement[key]) for key in ('reference_unix_s', 'reference_monotonic_s')):
                raise ValueError('UTC reference contains a nonfinite timestamp')
            now = UTCClock.reference_seconds(measurement, time.perf_counter())
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
            raise ClockReferenceUnavailable(f'UTC schedule verification unavailable; saved data are preserved: {error}') from error
    allowed, reason = resume_allowed(run, now)
    if not allowed:
        raise ValueError(reason)
    return measurement


def require_resume_when_ready(run, *, cancelled=None, timeout=600):
    """Allow the network time to connect at sign-in, while preserving terminal refusals."""
    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        if cancelled and cancelled():
            raise ResumeCancelled
        try:
            measurement = require_resume(run, clock_timeout=max(.001, min(8, deadline - time.monotonic())),
                                         require_verified=True)
        except ClockReferenceUnavailable:
            retry_at = min(deadline, time.monotonic() + retry_delay(attempt))
            while (wait := retry_at - time.monotonic()) > 0:
                if cancelled and cancelled():
                    raise ResumeCancelled
                time.sleep(min(wait, .25) if cancelled else wait)
            if time.monotonic() >= deadline:
                raise
            attempt += 1
        else:
            if cancelled and cancelled():
                raise ResumeCancelled
            return measurement


def validate_schedule(interval, seconds):
    if not math.isfinite(interval) or not 1 <= interval <= 86400:
        raise ValueError('Interval must be finite and within 1–86400 seconds')
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Duration must be finite and nonnegative')
