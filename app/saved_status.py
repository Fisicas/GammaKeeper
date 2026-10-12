"""Recover UI state from the journal even if the JSON cache is locked/stale."""
import datetime as dt
import json
import hashlib
from pathlib import Path
from export_run import readonly
from supervisor_store import read_health


def load_status(run):
    run = Path(run)
    # Never open status.json. GUI readers must not participate in its replacement
    # race, even when monitoring an older recorder. SQLite WAL is the status path.
    cached = None
    journal = None
    supervisor = None
    if (run/'run.sqlite').exists():
        db = readonly(run/'run.sqlite')
        try:
            row = db.execute("SELECT value FROM state WHERE key='ui_status'").fetchone()
            if row:
                journal = json.loads(row[0])
            recorder = db.execute("SELECT value FROM state WHERE key='recorder'").fetchone()
            supervisor = db.execute("SELECT value FROM state WHERE key='supervisor'").fetchone()
            supervisor = json.loads(supervisor[0]) if supervisor else None
            try:
                supervisor = read_health(run) or supervisor
            except (OSError, sqlite3.Error, ValueError):
                pass  # Legacy journal state remains usable if health reporting is locked.
            if not journal and (supervisor or recorder):
                manifest = json.loads((run/'manifest.json').read_text())
                rc = json.loads(recorder[0]) if recorder else {}
                sup = supervisor or {}
                last = db.execute('SELECT utc,kind,payload FROM events ORDER BY id DESC LIMIT 1').fetchone()
                message = ''
                if last:
                    payload = json.loads(last[2])
                    message = payload.get('error',last[1])
                journal = dict(mode=manifest['mode'],serial=manifest['serial'],run_path=str(run),
                               boot_id=rc.get('boot_id',sup.get('token')),pid=rc.get('pid',sup.get('child_pid',0)),
                               state=rc.get('status','starting'),utc=sup.get('utc',last[0] if last else manifest['created_utc']),
                               message=sup.get('reason',message))
                if journal['state']=='running':
                    journal['state']='recording'
            if journal and recorder:
                rc = json.loads(recorder[0])
                if rc['status'] in ('stopped','completed','error') and rc['boot_id']==journal.get('boot_id'):
                    journal['state'] = rc['status']
            state = journal if journal and (not cached or journal.get('utc','') >= cached.get('utc','')
                                          or journal['state'] in ('error','stopped','completed')) else cached
            if state:
                summary = db.execute("SELECT value FROM state WHERE key='acquisition_summary'").fetchone()
                if summary:
                    state['acquisition_summary'] = json.loads(summary[0])
                latest = db.execute('SELECT file,derived,sha256 FROM snapshots ORDER BY seq DESC LIMIT 1').fetchone()
                if latest:
                    try:
                        payload=(run/latest[0]).read_bytes()
                        if hashlib.sha256(payload).hexdigest()!=latest[2]:
                            raise ValueError('Raw preview SHA-256 mismatch')
                        snapshot = json.loads(payload)
                        state.update(last_snapshot=snapshot,last_interval=json.loads(latest[1]),snapshots=snapshot['seq']+1)
                    except (OSError,ValueError,KeyError,TypeError) as error:
                        state.pop('last_snapshot',None)
                        state['raw_preview_error']=f'{type(error).__name__}: {error}'
                if supervisor:
                    state['supervisor'] = supervisor
                    state['stop_request'] = supervisor.get('stop_request',state.get('stop_request'))
                return state
        finally:
            db.close()
    return cached
