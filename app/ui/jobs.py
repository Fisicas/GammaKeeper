"""Export, completion audit, backup and time-resolved job launching and polling."""
import datetime as dt
from pathlib import Path
import subprocess
import uuid

from PySide6 import QtCore, QtWidgets as Q
from runtime import python_command
from ui.paths import ROOT


class JobsMixin:
    def export(self):
        try:
            if not self.run or not (self.run / 'run.sqlite').exists():
                raise ValueError('Open or record a run first')
            self.export_destination = self.run / ('export_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
            self.export_log = (ROOT / ('export_' + uuid.uuid4().hex + '.log')).open('xb')
            self.export_process = subprocess.Popen([python_command(), '-X', 'utf8', str(ROOT / 'export_run.py'), str(self.run),
                                                    '--target', str(self.export_destination)], cwd=ROOT,
                                                   stdout=self.export_log, stderr=self.export_log, stdin=subprocess.DEVNULL,
                                                   creationflags=subprocess.CREATE_NO_WINDOW)
            self.export_button.setEnabled(False)
            self.message('Exporting a consistent snapshot of the saved run; recording can continue')
        except Exception as error:
            self.fail(error)

    def run_job(self,script,target,*extra):
        logs=self.analysis_root/'logs';logs.mkdir(exist_ok=True)
        log=logs/(script+'_'+uuid.uuid4().hex+'.log')
        with log.open('xb') as handle:
            args=[python_command(),'-X','utf8',str(self.analysis_root/script),str(self.run),*extra]
            process=subprocess.Popen(args,cwd=self.analysis_root,stdout=handle,stderr=handle,
                                     stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
        self.background_jobs.append(dict(process=process,target=target,log=log,script=script))
        self.message(f'{script}: started; result folder {target}')

    def audit_selected(self):
        if self.last_status.get('state')!='completed':
            self.fail('Completion validation is available after a scheduled run completes');return
        target=self.run/('completion_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
        self.run_job('completion.py',target,'--target',str(target))

    def backup_selected(self,resume=False):
        if not self.run:return
        location=Q.QFileDialog.getExistingDirectory(self,'Select unfinished backup folder' if resume else 'Choose backup destination parent')
        if not location:return
        target=Path(location) if resume else Path(location)/(self.run.name+'_backup_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
        self.run_job('backup_run.py',target,str(target))

    def poll_background_jobs(self):
        for job in list(self.background_jobs):
            if job['process'].poll() is not None:
                self.message(f"{job['script']}: {'complete' if job['process'].returncode==0 else 'failed; inspect log '+str(job['log'])}: {job['target']}")
                self.background_jobs.remove(job)
                if job['script']=='time_resolved.py':
                    ok=job['process'].returncode==0 and (job['target']/'COMPLETE.json').exists()
                    self.export_series_result.setText(('Export complete' if ok else 'Export failed; open the export log for details')+f": {job['target']}")
                    self.update_time_export()
                    QtCore.QTimer.singleShot(0,lambda:self.export_scroll.ensureWidgetVisible(self.export_open_log))
            elif job['script']=='time_resolved.py':
                try:
                    with job['log'].open('rb') as handle:
                        handle.seek(max(0,job['log'].stat().st_size-2048))
                        lines=handle.read().decode('utf-8',errors='replace').splitlines()
                    progress=lines[-1] if lines else 'Checking source integrity…'
                    self.export_series_result.setText(f'Export running: {progress}\nOutput: {job["target"]}')
                except OSError:
                    pass
