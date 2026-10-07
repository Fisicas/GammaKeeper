"""Live validation: cache lock, USB reopen, recorder crash, automatic recovery."""
import bootstrap
import datetime as dt
import json
from pathlib import Path
import subprocess
import time
from runtime import python_command
from saved_status import load_status
from audit_live import audit
from storage import immutable_json

ROOT=Path(__file__).resolve().parent
revision=1
while (ROOT/'validation'/f'live_fault_pilot_v{revision:02d}').exists():
    revision+=1
run=ROOT/'validation'/f'live_fault_pilot_v{revision:02d}'
log=(ROOT/'validation'/f'live_fault_pilot_v{revision:02d}.log').open('xb')
process=subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'supervisor.py'),'--run',str(run),
                          '--serial','RC-103-006489','--seconds','180','--interval','10',
                          '--test-reconnect-at','40','--label','v0.2 live validation: cache lock, USB reopen, killed recorder'],
                         cwd=ROOT,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
started=time.monotonic()
held=None
killed=False
observed=[]
try:
    while process.poll() is None:
        elapsed=time.monotonic()-started
        if elapsed>240:
            raise TimeoutError('Live validation exceeded deadline')
        if elapsed>=15 and held is None and elapsed<60 and (run/'status.json').exists():
            held=(run/'status.json').open('rb')
            held.read(1)
            print('Holding legacy status file open; acquisition must continue',flush=True)
        if elapsed>=60 and held:
            held.close();held=None
            print('Released legacy status file',flush=True)
        state=load_status(run) if (run/'run.sqlite').exists() else None
        if state:
            observed.append(dict(elapsed_s=elapsed,state=state['state'],snapshots=state.get('snapshots',0),
                                 retries=state.get('retries',0),pid=state['pid'],
                                 supervisor_restarts=(state.get('supervisor') or {}).get('restarts')))
        if elapsed>=90 and not killed and state and state.get('snapshots',0)>=6:
            # Kill only the verified recorder in this new disposable test run.
            subprocess.run(['taskkill','/PID',str(state['pid']),'/T','/F'],check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            killed=True
            print('Killed live recorder; supervisor must resume automatically',flush=True)
        time.sleep(.5)
    if process.returncode:
        raise RuntimeError(f'Supervisor exited {process.returncode}; see test log')
    report=audit(run)
    report.update(injected_status_lock=True,injected_recorder_crash=killed,observed_states=observed,
                  final_state=load_status(run),qualification='Three-minute connected-device fault pilot; not an overnight soak')
    assert report['count_conservation'] and report['calibration_unchanged']
    assert report['final_state']['supervisor']['restarts']>=1
    assert report['final_state']['state']=='completed'
    assert report['event_counts'].get('status_cache_unavailable',0)>0
    assert not report['decoder_warnings']
    immutable_json(ROOT/'validation'/f'live_fault_pilot_v{revision:02d}_report.json',report)
    print(json.dumps(dict(snapshots=report['snapshot_count'],added_counts=report['added_counts'],
                         conserved=report['count_conservation'],event_counts=report['event_counts'],
                         supervisor_restarts=report['final_state']['supervisor']['restarts']),indent=2),flush=True)
finally:
    if held:
        held.close()
    if process.poll() is None:
        state=load_status(run)
        if state and state.get('stop_request'):
            Path(state['stop_request']).write_text('validation cleanup stop')
            process.wait(timeout=25)
    log.close()
