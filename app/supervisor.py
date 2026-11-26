"""Restart unexpected recorder exits; never auto-restart a durable-data failure."""
import bootstrap
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid

from analysis import retry_delay
from runtime import PYTHONW, python_command
from storage import DeviceMutex
from supervisor_store import read_health, save_health
from run_policy import require_resume, require_resume_when_ready
import reboot_task
from diagnostics import diagnostic
from recovery_progress import read as read_recovery
from session_end import start_watcher
from system_time import boot_identity

ROOT = Path(__file__).resolve().parent


def save_supervisor_state(run, state):
    save_health(run, state)


def inspect_run(run):
    try:
        db = sqlite3.connect((run/'run.sqlite').as_uri()+'?mode=ro',uri=True,timeout=.2)
        try:
            recorder = db.execute("SELECT value FROM state WHERE key='recorder'").fetchone()
            # Device absence produces retry events at <=75s intervals. A stalled
            # recorder has no journal progress; GUI-cache activity is not progress.
            progress = tuple(db.execute(f'SELECT max({field}) FROM {table}').fetchone()[0]
                             for table,field in [('events','id'),('telemetry','id'),('snapshots','seq')])
            return (json.loads(recorder[0]) if recorder else None),progress
        finally:
            db.close()
    except (OSError,sqlite3.Error,ValueError):
        return None,None


class ProgressWatchdog:
    def __init__(self, timeout=180, startup_grace=300):
        self.timeout = timeout
        self.startup_grace = startup_grace
        self.started = time.monotonic()
        self.last_progress = self.started
        self.token = None

    def observe(self, token, now=None):
        now = time.monotonic() if now is None else now
        if token is not None and token != self.token:
            self.token = token
            self.last_progress = now
        grace = self.startup_grace if self.token is None else self.timeout
        return now-self.last_progress>grace


class StreamWatchdog:
    """Independent monotonic stream ages; reconnect backoff is intentional."""
    def __init__(self, interval, timeout=180, now=None):
        self.started = time.monotonic() if now is None else now
        self.last = dict(spectrum=self.started, telemetry=self.started)
        self.tokens = dict(spectrum=None, telemetry=None)
        self.thresholds = dict(spectrum=max(30., interval * 3 + 15), telemetry=30.)
        self.overall = ProgressWatchdog(timeout, max(300, timeout))

    def observe(self, progress, mode, now=None):
        now = time.monotonic() if now is None else now
        recovering=mode=='recovering'
        if recovering:
            # Real per-file verification progress is a valid startup heartbeat.
            self.overall.startup_grace=max(300,self.overall.startup_grace)
        stalled = self.overall.observe(progress, now)
        if progress is not None:
            for stream, value in (('spectrum', progress[2]), ('telemetry', progress[1])):
                if value != self.tokens[stream]:
                    self.tokens[stream] = value
                    self.last[stream] = now
        if mode != 'recording':
            self.last = dict(spectrum=now, telemetry=now)
            return 'journal' if stalled else None
        for stream in ('spectrum', 'telemetry'):
            if now - self.last[stream] > self.thresholds[stream]:
                return stream
        return 'journal' if stalled else None


def read_mode(run):
    try:
        db = sqlite3.connect((run/'run.sqlite').as_uri()+'?mode=ro', uri=True, timeout=.1)
        try:
            row = db.execute("SELECT value FROM state WHERE key='ui_status'").fetchone()
            return json.loads(row[0]).get('state') if row else None
        finally:
            db.close()
    except (OSError, sqlite3.Error, ValueError):
        return None


def interrupted_for_reboot(run, health, recorder, current_boot):
    """Return false on this boot; refuse known terminal or untrusted saved state."""
    if (run/'BACKUP_PLAN.json').exists():
        raise ValueError('This is a preserved backup snapshot.')
    if not health or not health.get('recover_after_restart'):
        raise ValueError('This run has no automatic restart request.')
    if health.get('status') not in ('running', 'restart_wait', 'host_shutdown'):
        raise ValueError('The supervisor was no longer managing an active recording.')
    if not recorder or recorder.get('status') not in ('running', 'interrupted_by_shutdown'):
        raise ValueError('The recorder was no longer collecting this run.')
    stop = health.get('stop_request')
    if stop and Path(stop).parent == run and Path(stop).exists():
        raise ValueError('The user requested Stop before Windows restarted.')
    recorder_boot = recorder.get('boot_id')
    if recorder_boot and (run/f'stop_{recorder_boot}.request').exists():
        raise ValueError('The user requested Stop before Windows restarted.')
    old_boots = health.get('system_boot_id'), recorder.get('system_boot_id')
    return bool(current_boot and all(old_boots) and current_boot not in old_boots)


def set_reboot_task(run, state, enabled):
    """OS scheduling is optional and cannot stop acquisition."""
    status = 'armed' if enabled else 'removed'
    try:
        (reboot_task.register if enabled else reboot_task.remove)(run)
    except (OSError, ValueError) as error:
        state['restart_recovery_error'] = (
            f'Windows restart recovery could not be {"armed" if enabled else "removed"}: {error}')
        event = diagnostic('restart_recovery_task_failed', action=status, error=str(error),
                           task=reboot_task.task_name(run))
    else:
        state['restart_recovery_armed'] = enabled
        if enabled:
            state.pop('restart_recovery_error', None)
        event = diagnostic('restart_recovery_task_changed', status=status, task=reboot_task.task_name(run))
    if not (run/'BACKUP_PLAN.json').exists():
        save_health(run, state, event)


def open_recovered_window(run, data_root=None):
    env = dict(os.environ, GAMMAKEEPER_DATA_ROOT=data_root) if data_root else None
    executable = str(PYTHONW) if PYTHONW.is_file() else python_command()
    subprocess.Popen([executable, '-X', 'utf8', str(ROOT/'gui.py'), '--inspect-run', str(run)],
                     cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)


def supervise(arguments, max_restarts=3, stall_timeout=180):
    arguments = list(arguments)
    run = Path(arguments[arguments.index('--run')+1]).resolve()
    automatic = '--auto-resume' in arguments
    requested = '--recover-after-restart' in arguments
    opted_out = '--no-recover-after-restart' in arguments
    arguments = [arg for arg in arguments if arg not in (
        '--auto-resume', '--recover-after-restart', '--no-recover-after-restart')]
    recovery_data_root = None
    if automatic:
        if '--recovery-data-root' in arguments:
            recovery_data_root = arguments[arguments.index('--recovery-data-root')+1]
        arguments = ['--run', str(run), '--resume', '--wait-for-resume-clock']
    resuming = '--resume' in arguments
    try:
        if resuming:
            serial = json.loads((run/'manifest.json').read_text(encoding='utf-8'))['serial']
            if not automatic:
                require_resume(run)
        else:
            serial = ('SIMULATED' if '--demo' in arguments else
                      arguments[arguments.index('--serial')+1] if '--serial' in arguments else 'UNKNOWN')
        mutex = DeviceMutex(serial+'_supervisor')
    except (OSError, sqlite3.Error, ValueError, KeyError, RuntimeError) as error:
        diagnostic('resume_refused' if resuming else 'supervisor_start_refused', error=str(error), run=str(run))
        return 2
    try:
        prior = None
        if resuming:
            try:
                prior = read_health(run)
            except (OSError, sqlite3.Error, ValueError) as error:
                diagnostic('supervisor_history_unavailable', error=str(error), run=str(run))
                if automatic:
                    return 2
        if automatic:
            try:
                if not interrupted_for_reboot(run, prior, inspect_run(run)[0], boot_identity()):
                    return 0
                require_resume_when_ready(run)
                # A Stop request can arrive while the network clock check is retrying.
                if not interrupted_for_reboot(run, prior, inspect_run(run)[0], boot_identity()):
                    return 0
            except (OSError, sqlite3.Error, ValueError, KeyError) as error:
                event = diagnostic('automatic_resume_refused', error=str(error), run=str(run))
                # Leave acquisition and its journal alone. Persist the reason for the manual GUI.
                prior = dict(prior or {}, restart_recovery_error=str(error))
                if not (run/'BACKUP_PLAN.json').exists():
                    save_health(run, prior, event)
                set_reboot_task(run, prior, False)
                return 2
        prior = prior or {}
        enabled = not opted_out and (automatic or requested or bool(prior.get('recover_after_restart')))
        task_exists = automatic or bool(prior.get('restart_recovery_armed'))
        return supervise_run(arguments, run, enabled, task_exists, automatic, max_restarts, stall_timeout,
                             recovery_data_root)
    finally:
        mutex.close()


def supervise_run(arguments, run, recover_after_restart, task_exists, automatic, max_restarts, stall_timeout,
                  recovery_data_root=None):
    monitor_config = (json.loads(arguments[arguments.index('--monitor-config')+1]) if '--monitor-config' in arguments
                      else json.loads((run/'manifest.json').read_text()).get('monitoring',{}) if (run/'manifest.json').exists() else {})
    if monitor_config.get('enabled') and os.environ.get('GAMMAKEEPER_TEST_MODE')!='1':
        try:
            subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'monitor.py'),str(run)],cwd=ROOT,
                             stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)
        except (OSError,ValueError) as error:
            diagnostic('optional_monitor_start_failed',error=str(error))
    token = uuid.uuid4().hex
    stop = run/f'stop_supervisor_{token}.request'
    state = dict(pid=os.getpid(),token=token,status='starting',restarts=0,stop_request=str(stop),
                 utc=dt.datetime.now(dt.timezone.utc).isoformat(),system_boot_id=boot_identity(),
                 recover_after_restart=recover_after_restart, restart_recovery_armed=task_exists)
    # Session end: never restart or kill the recorder while Windows signs out or restarts.
    watcher = start_watcher(f'GammaKeeper supervisor {run.name}')
    host_ending = False
    signing_out = False
    shutdown_requested_at = None
    arm_pending = recover_after_restart and not automatic
    show_pending = automatic
    if task_exists and not recover_after_restart:
        set_reboot_task(run, state, False)
    logs = ROOT/'logs'
    logs.mkdir(exist_ok=True)
    (ROOT/'validation').mkdir(exist_ok=True)
    first = True
    rapid_failures = 0
    child = None
    rc = 0

    def record_session_events(now):
        nonlocal shutdown_requested_at, signing_out
        if not watcher:
            return
        for kind, details in watcher.drain():
            if kind == 'host_shutdown_requested':
                shutdown_requested_at = now
            elif kind == 'host_shutdown_cancelled':
                shutdown_requested_at = None
            elif kind == 'host_shutdown':
                signing_out = 'logoff' in details.get('flags', ())
            save_health(run, state, diagnostic(kind, role='supervisor', **details))

    try:
        while True:
            if stop.exists():
                break
            child_args = list(arguments) if first else ['--run',str(run),'--resume']
            if automatic and '--wait-for-resume-clock' not in child_args:
                child_args.append('--wait-for-resume-clock')
            previous_recorder,previous_progress=inspect_run(run)
            previous_boot=(previous_recorder or {}).get('boot_id')
            child_args += ['--stop-request',str(stop)]
            log = logs/(dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_recorder.log')
            with log.open('xb') as output:
                child = subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'recorder.py'),*child_args],
                                         cwd=ROOT,stdin=subprocess.DEVNULL,stdout=output,stderr=output,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
            state.update(status='running',child_pid=child.pid,child_log=str(log),utc=dt.datetime.now(dt.timezone.utc).isoformat())
            started = time.monotonic()
            from time_units import parse_time
            interval = (parse_time(arguments[arguments.index('--interval')+1]) if '--interval' in arguments
                        else json.loads((run/'manifest.json').read_text())['interval_s'] if (run/'manifest.json').exists() else 10.)
            watchdog = StreamWatchdog(interval, stall_timeout)
            recovery_grace=max(300., ((previous_progress or (None,None,0))[2] or 0)*.05)
            if automatic:
                recovery_grace += 620.
            last_persist = -float('inf')
            while child.poll() is None:
                now = time.monotonic()
                if watcher:
                    record_session_events(now)
                    if watcher.ending.is_set():
                        record_session_events(now)
                        host_ending = True
                        break
                if now-last_persist >= 2:
                    state['utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
                    save_supervisor_state(run,state)
                    last_persist = now
                    recorder_state,progress = inspect_run(run)
                    recovery=read_recovery(run)
                    mode=read_mode(run)
                    ready=recorder_state and recorder_state.get('boot_id')!=previous_boot and recorder_state.get('status')=='running'
                    if ready and not stop.exists():
                        if arm_pending and progress and progress[2] is not None:
                            arm_pending = False
                            if state['system_boot_id']:
                                set_reboot_task(run, state, True)
                            else:
                                state['restart_recovery_error'] = 'Windows boot identity unavailable; restart recovery could not be armed.'
                                save_supervisor_state(run, state)
                        if show_pending:
                            show_pending = False
                            try:
                                open_recovered_window(run, recovery_data_root)
                            except (OSError, ValueError) as error:
                                save_health(run, state, diagnostic('restart_recovery_window_failed', error=str(error)))
                    if not ready:
                        mode='recovering'
                        watchdog.overall.timeout=recovery_grace
                    else:
                        watchdog.overall.timeout=stall_timeout
                    if recovery and recovery['phase']=='running' and 0<=now-recovery['monotonic_s']<10:
                        mode='recovering'
                        progress=tuple(progress or (None,None,None))+(recovery['verified'],)
                        state['recovery_progress']=recovery
                    elif 'recovery_progress' in state:
                        state.pop('recovery_progress',None)
                    stalled = watchdog.observe(progress,mode,now)
                    if stalled:
                        state['watchdog_reason'] = f'{stalled} stream stopped advancing within its watchdog deadline'
                        event = diagnostic('stream_stalled', stream=stalled, progress=progress)
                        save_health(run,state,event)
                        # Kill only this supervisor's child tree. Windows venv
                        # redirectors can have another Python process below them.
                        terminate_tree(child.pid)
                if stop.exists() and now-started>1:
                    # Recorder sees the same stable stop file across restarts.
                    state['status'] = 'stopping'
                time.sleep(.2)
            if watcher and not host_ending:
                # The recorder can finish its shutdown before this loop's next poll; read what arrived meanwhile.
                record_session_events(time.monotonic())
                host_ending = watcher.ending.is_set()
            last_recorder = inspect_run(run)[0] or {}
            if (not host_ending and last_recorder.get('boot_id') != previous_boot
                    and last_recorder.get('status') == 'interrupted_by_shutdown'):
                host_ending = True  # this child, rather than the prior recorder, saw the session end
            if (not host_ending and child.returncode != 2 and shutdown_requested_at is not None
                    and time.monotonic()-shutdown_requested_at < 60):
                host_ending = True  # the recorder ended during a sign-out/restart; do not restart it
            if host_ending:
                try:
                    child.wait(timeout=3.0)  # the recorder is saving its final snapshot
                except subprocess.TimeoutExpired:
                    pass
                state.update(status='host_shutdown',last_exit_code=child.poll(),utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                             reason='Windows session ended; resume the run after signing in if its schedule is still open')
                if signing_out:
                    # A same-boot sign-out does not authorize a later automatic restart.
                    state['recover_after_restart'] = False
                elif state['restart_recovery_armed'] and not stop.exists():
                    state['reason'] = 'Windows session ended; GammaKeeper will check this run after restart and sign-in'
                break
            rc = child.returncode
            _,last_progress = inspect_run(run)
            state['last_durable_progress'] = last_progress
            state.update(last_exit_code=rc,utc=dt.datetime.now(dt.timezone.utc).isoformat())
            if stop.exists() or rc==0:
                state['status'] = 'stopped'
                if rc == 0 and (run/'run.sqlite').exists():
                    recorder_state, _ = inspect_run(run)
                    if recorder_state and recorder_state.get('status') == 'completed':
                        state['completion_validation'] = 'running'
                        state['completion_target'] = str(run/('completion_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')))
                        seal_log = logs/(dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_completion.log')
                        with seal_log.open('xb') as output:
                            result = subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'completion.py'),str(run),
                                                       '--target',state['completion_target']],
                                                      cwd=ROOT,stdout=output,stderr=output,
                                                      creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
                        state.update(completion_pid=result.pid, completion_log=str(seal_log))
                break
            if rc==2:
                state.update(status='attention_required',reason='Durable storage, integrity, or validation failure; automatic restart suppressed')
                break
            rapid_failures = 0 if time.monotonic()-started>=60 else rapid_failures
            rapid_failures += 1
            if rapid_failures>max_restarts or not (run/'manifest.json').exists():
                state.update(status='attention_required',reason='Repeated unexpected exits or startup failure; see child log')
                break
            delay = retry_delay(rapid_failures-1)
            state.update(status='restart_wait',restarts=state['restarts']+1,retry_delay_s=delay,
                         reason='Unexpected recorder exit; verify raw data and resume',utc=dt.datetime.now(dt.timezone.utc).isoformat())
            save_supervisor_state(run,state)
            deadline = time.monotonic()+delay
            while time.monotonic()<deadline and not stop.exists():
                if watcher and watcher.ending.is_set():
                    break
                time.sleep(.1)
            if watcher and watcher.ending.is_set():
                host_ending = True
                record_session_events(time.monotonic())
                state.update(status='host_shutdown', reason='Windows session ended before the recorder could restart')
                if signing_out:
                    state['recover_after_restart'] = False
                break
            first = False
        save_supervisor_state(run,state)
        if state['restart_recovery_armed'] and (not host_ending or signing_out or stop.exists()):
            set_reboot_task(run, state, False)
        return rc
    finally:
        if child and child.poll() is None and not host_ending:
            terminate_tree(child.pid)
        if watcher:
            watcher.close()


def terminate_tree(pid):
    subprocess.run(['taskkill','/PID',str(int(pid)),'/T','/F'],stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)


if __name__=='__main__':
    raise SystemExit(supervise(sys.argv[1:]))
