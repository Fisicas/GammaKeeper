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
from runtime import python_command
from storage import DeviceMutex
from supervisor_store import save_health
from run_policy import require_resume
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


def supervise(arguments, max_restarts=3, stall_timeout=180):
    run = Path(arguments[arguments.index('--run')+1]).resolve()
    serial = arguments[arguments.index('--serial')+1] if '--serial' in arguments else 'RC-103-006489'
    if '--resume' in arguments:
        try:
            require_resume(run)
        except (OSError, ValueError, KeyError) as error:
            diagnostic('resume_refused', error=str(error), run=str(run))
            return 2
        serial = json.loads((run/'manifest.json').read_text())['serial']
    elif '--demo' in arguments:
        serial = 'SIMULATED'
    mutex = DeviceMutex(serial+'_supervisor')
    token = uuid.uuid4().hex
    stop = run/f'stop_supervisor_{token}.request'
    state = dict(pid=os.getpid(),token=token,status='starting',restarts=0,stop_request=str(stop),
                 utc=dt.datetime.now(dt.timezone.utc).isoformat(),system_boot_id=boot_identity())
    # Session end: never restart or kill the recorder while Windows signs out or restarts.
    watcher = start_watcher(f'GammaKeeper supervisor {run.name}')
    host_ending = False
    shutdown_requested_at = None
    logs = ROOT/'logs'
    logs.mkdir(exist_ok=True)
    (ROOT/'validation').mkdir(exist_ok=True)
    first = True
    rapid_failures = 0
    child = None
    rc = 0
    try:
        while True:
            if stop.exists():
                break
            child_args = list(arguments) if first else ['--run',str(run),'--resume']
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
            last_persist = -float('inf')
            while child.poll() is None:
                now = time.monotonic()
                if watcher:
                    for kind,details in watcher.drain():
                        shutdown_requested_at = (now if kind=='host_shutdown_requested' else
                                                 None if kind=='host_shutdown_cancelled' else shutdown_requested_at)
                        save_health(run,state,diagnostic(kind,role='supervisor',**details))
                    if watcher.ending.is_set():
                        for kind,details in watcher.drain():  # notifications queued just before the flag
                            save_health(run,state,diagnostic(kind,role='supervisor',**details))
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
                for kind,details in watcher.drain():
                    shutdown_requested_at = (time.monotonic() if kind=='host_shutdown_requested' else
                                             None if kind=='host_shutdown_cancelled' else shutdown_requested_at)
                    save_health(run,state,diagnostic(kind,role='supervisor',**details))
                host_ending = watcher.ending.is_set()
            if not host_ending and (inspect_run(run)[0] or {}).get('status')=='interrupted_by_shutdown':
                host_ending = True  # the recorder itself saw the session end
            if not host_ending and shutdown_requested_at is not None and time.monotonic()-shutdown_requested_at<60:
                host_ending = True  # the recorder ended during a sign-out/restart; do not restart it
            if host_ending:
                try:
                    child.wait(timeout=3.0)  # the recorder is saving its final snapshot
                except subprocess.TimeoutExpired:
                    pass
                state.update(status='host_shutdown',last_exit_code=child.poll(),utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                             reason='Windows session ended; resume the run after signing in if its schedule is still open')
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
                time.sleep(.1)
            first = False
        save_supervisor_state(run,state)
        return rc
    finally:
        if child and child.poll() is None and not host_ending:
            terminate_tree(child.pid)
        mutex.close()
        if watcher:
            watcher.close()


def terminate_tree(pid):
    subprocess.run(['taskkill','/PID',str(int(pid)),'/T','/F'],stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW,timeout=10)


if __name__=='__main__':
    raise SystemExit(supervise(sys.argv[1:]))
