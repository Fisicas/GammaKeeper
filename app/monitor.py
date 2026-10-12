"""Persistent local tray monitor: alerts, hourly QC and opt-in dated backups, no device access."""
import bootstrap
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from analysis import timestamp
from experiment_metadata import monitoring
from runtime import python_command
from saved_status import load_status
from storage import DeviceMutex
from system_time import boot_identity
from winfiles import process_alive

ROOT=Path(__file__).resolve().parent


def conditions(state,now,config):
    result={}
    sup=state.get('supervisor') or {}
    mode=state.get('state','starting')
    if mode in ('error','stopped','interrupted_by_shutdown') or sup.get('status')=='attention_required':
        result['stopped']='Recording stopped or interrupted. Open GammaKeeper to inspect and resume if the schedule permits.'
    elif mode in ('recording','connecting','reconnecting'):
        if state.get('system_boot_id') and state['system_boot_id']!=boot_identity():
            result['stopped']='Windows restarted; acquisition needs inspection/resume.'
        elif state.get('pid') and not process_alive(state['pid']) and sup.get('status') not in ('starting','restart_wait'):
            result['stopped']='The recorder process exited; inspect its log.'
        if now-timestamp(state['utc'])>max(45.,state.get('interval_s',10)*3+15):
            result['stale']='Acquisition status is stale; inspect the recorder and supervisor.'
        if mode in ('connecting','reconnecting'):
            result['disconnected']='Detector remains disconnected. Recovery may retain aggregate counts if its counters remain continuous.'
    if state.get('raw_preview_error'):result['integrity']='Raw spectrum integrity needs attention: '+state['raw_preview_error']
    return result


class AlertPolicy:
    def __init__(self,notified=()):
        self.notified=set(notified);self.since={}

    def update(self,active,now,disconnect_seconds=120):
        self.notified.intersection_update(active)
        for key in list(self.since):
            if key not in active:self.since.pop(key)
        ready=[]
        for key,text in active.items():
            self.since.setdefault(key,now)
            delay=disconnect_seconds if key in ('disconnected','stale') else 0.
            if key not in self.notified and now-self.since[key]>=delay:
                ready.append((key,text));self.notified.add(key)
        return ready


def read_monitor(run):
    path=Path(run).resolve()/'monitor.sqlite'
    if not path.exists():return {}
    try:db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.1)
    except (OSError,sqlite3.Error):return {}
    try:
        row=db.execute("SELECT payload FROM state WHERE key='health'").fetchone()
        return json.loads(row[0]) if row else {}
    except (sqlite3.Error,ValueError):return {}
    finally:db.close()


class Monitor:
    def __init__(self,run,notify=lambda title,text:False):
        self.run=Path(run).resolve();self.notify=notify
        self.db=sqlite3.connect(self.run/'monitor.sqlite',timeout=2)
        self.db.executescript('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,payload TEXT); CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,utc TEXT,kind TEXT,payload TEXT);')
        row=self.db.execute("SELECT payload FROM state WHERE key='health'").fetchone()
        self.health=json.loads(row[0]) if row else {}
        if self.health.get('system_boot_id')!=boot_identity():self.health.pop('backup_attempt_mono',None)
        self.health['system_boot_id']=boot_identity()
        self.policy=AlertPolicy(self.health.get('notified',[]))
        self.job=None;self.last_qc_poll=-float('inf')
        self.started=time.monotonic()

    def event(self,kind,**payload):
        with self.db:self.db.execute('INSERT INTO events(utc,kind,payload) VALUES(?,?,?)',
            (dt.datetime.now(dt.timezone.utc).isoformat(),kind,json.dumps(payload)))

    def launch(self,script,target,*extra):
        logs=self.run/'logs';logs.mkdir(exist_ok=True)
        with (logs/(target.name+'.log')).open('xb') as handle:
            child=subprocess.Popen([python_command(),'-X','utf8',str(ROOT/script),str(self.run),*extra],cwd=ROOT,
                stdout=handle,stderr=handle,stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        self.job=dict(process=child,script=script,target=target)
        self.event('job_started',script=script,target=str(target))

    def poll(self):
        config=monitoring(self.run)
        state=load_status(self.run) or {}
        if not state:return False
        active=conditions(state,time.time(),config)
        free=shutil.disk_usage(self.run).free
        self.health['volume_free_bytes']=free
        if free<1024**3:active['low_space']='Recording volume has less than 1 GiB free; reserve space for raw data and exports.'
        if self.job and self.job['process'].poll() is not None:
            job=self.job;self.job=None
            ok=job['process'].returncode==0
            self.event('job_finished',script=job['script'],ok=ok,target=str(job['target']))
            if job['script']=='backup_run.py':
                self.health['backup_error']=None if ok else 'Automatic backup failed; inspect monitor logs.'
                if ok:
                    marker=json.loads((job['target']/'BACKUP_COMPLETE.json').read_text())
                    self.health['last_backup']=dict(completed_utc=marker['completed_utc'],snapshots=marker['snapshots'],
                                                   target=str(job['target']),verified=True)
            else:
                self.health['qc_error']=None if ok else 'Scientific QC job failed; inspect monitor logs.'
                if ok:
                    qc=json.loads((job['target']/'SCIENTIFIC_QC.json').read_text())
                    self.health['last_qc']=dict(source_head=qc['source_head'],target=str(job['target']),
                        completed_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                        gain_attention=any(p.get('drift_attention') for r in qc['rows'][-4:] for p in r.get('peaks',{}).values()))
        if self.health.get('backup_error'):active['backup_failed']=self.health['backup_error']
        if self.health.get('qc_error'):active['qc_failed']=self.health['qc_error']
        if (self.health.get('last_qc') or {}).get('gain_attention'):active['gain_drift']='A qualified advisory centroid shifted by more than 5 keV; inspect temperature and peak fits.'
        if state.get('state')=='completed':
            active['completed']='Acquisition completed. Integrity and scientific completeness are checked separately.'
            target=(state.get('supervisor') or {}).get('completion_target')
            if target and (Path(target)/'VALIDATION.json').exists():
                verdict=json.loads((Path(target)/'VALIDATION.json').read_text())
                self.health['completion_audit']=dict(passed=verdict.get('passed'),data_quality=verdict.get('data_quality'),target=target)
                if not verdict.get('passed') or verdict.get('data_quality')=='review_required':
                    active['audit_review']='Completed-run audit requires review. Open the validation report.'
            elif target and time.monotonic()-self.started>30 and not process_alive((state.get('supervisor') or {}).get('completion_pid',0)):
                self.health['completion_audit']=dict(passed=False,data_quality='audit_unavailable',target=target)
                active['audit_review']='Completion audit process exited without a verdict; inspect its log.'
            elif not target and time.monotonic()-self.started>30:
                self.health['completion_audit']=dict(passed=False,data_quality='audit_unavailable',target=None)
                active['audit_review']='No completion audit is available; use Validate completed run in GammaKeeper.'
        for key,text in self.policy.update(active,time.monotonic(),config['disconnect_warn_s']):
            delivered=bool(self.notify(self.run.name,text)) if config['enabled'] and config['notifications'] else False
            self.event('alert',code=key,text=text,notification_requested=delivered,delivery_confirmation='unavailable')
        self.health.update(utc=dt.datetime.now(dt.timezone.utc).isoformat(),pid=os.getpid(),notified=sorted(self.policy.notified),
                           active_conditions=active,config=config,job=self.job['script'] if self.job else None)
        with self.db:self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('health',json.dumps(self.health)))
        if config['enabled'] and not self.job:
            last=self.health.get('last_backup') or {}
            terminal=state.get('state') in ('completed','stopped','error','interrupted_by_shutdown')
            final_backup_needed=terminal and last.get('snapshots',0)<state.get('snapshots',0)
            due=config['backup_parent'] and (not last or time.time()-timestamp(last['completed_utc'])>=config['backup_interval_s'] or final_backup_needed)
            if due and time.monotonic()-self.health.get('backup_attempt_mono',-float('inf'))>300:
                self.health['backup_attempt_mono']=time.monotonic()
                parent=Path(config['backup_parent']).resolve()
                if parent==self.run or parent.is_relative_to(self.run):
                    self.health['backup_error']='Backup destination is inside the source recording.'
                elif not parent.exists():
                    self.health['backup_error']='Backup destination is unavailable.'
                elif shutil.disk_usage(parent).free<512*1024**2:
                    self.health['backup_error']='Backup destination has less than 512 MiB free.'
                else:
                    target=parent/(self.run.name+'_backup_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
                    self.launch('backup_run.py',target,str(target))
            elif time.monotonic()-self.last_qc_poll>=3600 and state.get('last_snapshot') and state.get('mode')!='demo':
                self.last_qc_poll=time.monotonic()
                target=self.run/('qc_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
                self.launch('scientific_qc.py',target,'--target',str(target),'--cache-root',str(ROOT/'cache'/'scientific'))
        # Persist launch/error state too; neither alerts nor backups depend on a GUI.
        with self.db:self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('health',json.dumps(self.health)))
        terminal=state.get('state') in ('completed','stopped','error','interrupted_by_shutdown')
        last=self.health.get('last_backup') or {}
        backup_done=not config['enabled'] or not config['backup_parent'] or last.get('snapshots',0)>=state.get('snapshots',0) or self.health.get('backup_error')
        audit_done=state.get('state')!='completed' or self.health.get('completion_audit')
        return bool(terminal and not self.job and backup_done and audit_done and time.monotonic()-self.started>15)

    def close(self):
        self.db.close()  # Background jobs have their own processes and keep their versioned outputs.


def main():
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);args=parser.parse_args()
    deadline=time.monotonic()+120
    while not (args.run/'run.sqlite').exists():
        if time.monotonic()>deadline:return 2
        time.sleep(.5)
    serial=json.loads((args.run/'manifest.json').read_text())['serial']
    try:mutex=DeviceMutex(serial+'_monitor_'+hashlib.sha256(str(args.run.resolve()).encode()).hexdigest()[:12])
    except RuntimeError:return 0
    from PySide6 import QtCore,QtWidgets as Q
    from ui.splash import app_icon
    app=Q.QApplication([]);app.setQuitOnLastWindowClosed(False)
    tray=Q.QSystemTrayIcon(app_icon());tray.setToolTip('GammaKeeper monitor • '+args.run.name)
    menu=Q.QMenu();open_action=menu.addAction('Open recording');quit_action=menu.addAction('Close monitor (recording continues)')
    open_action.triggered.connect(lambda:subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'gui.py'),'--inspect-run',str(args.run)],
        cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW))
    quit_action.triggered.connect(app.quit);tray.setContextMenu(menu)
    if Q.QSystemTrayIcon.isSystemTrayAvailable():tray.show()
    def notify(title,text):
        if os.environ.get('GAMMAKEEPER_TEST_MODE')=='1' or not tray.isVisible():return False
        tray.showMessage('GammaKeeper • '+title,text,Q.QSystemTrayIcon.MessageIcon.Information,10000);return True
    monitor=Monitor(args.run,notify)
    timer=QtCore.QTimer()
    def poll():
        try:
            if monitor.poll():app.quit()
        except Exception as error:monitor.event('monitor_error',error=f'{type(error).__name__}: {error}')
    timer.timeout.connect(poll);timer.start(5000);poll()
    try:return app.exec()
    finally:monitor.close();mutex.close()


if __name__=='__main__':raise SystemExit(main())
