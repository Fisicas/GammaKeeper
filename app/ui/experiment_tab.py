"""Experiment progress, versioned sample sheets, QC and a read-only run browser."""
import datetime as dt
import json
from pathlib import Path
import threading
import time
import numpy as np
from PySide6 import QtCore,QtWidgets as Q
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import annotations
from experiment_metadata import FIELDS,latest,save_sheet,monitoring
from monitor import read_monitor
from scientific_qc import progress
from ui.paths import ROOT,DATA_ROOT
from ui.theme import TEAL,GOLD,BG,MUTED
from ui.stable_text import set_text
from run_health import disk_budget


class ScienceSignals(QtCore.QObject):
    done=QtCore.Signal(object)


class ScienceTask(QtCore.QRunnable):
    def __init__(self,run,cutoff,cancel):
        super().__init__();self.run=run;self.cutoff=cutoff;self.cancel=cancel;self.signals=ScienceSignals()
    def run(self):
        try:message=dict(run=str(self.run),result=progress(self.run,ROOT/'cache'/'scientific',self.cutoff,self.cancel.is_set),error=None)
        except Exception as error:message=dict(run=str(self.run),result=None,error=str(error))
        self.signals.done.emit(message)


class SampleDialog(Q.QDialog):
    def __init__(self,parent,values):
        super().__init__(parent);self.setWindowTitle('Sample sheet • every save creates a new version')
        layout=Q.QVBoxLayout(self);form=Q.QFormLayout();self.fields={}
        for name,label in FIELDS:
            edit=Q.QLineEdit(str(values.get(name,'')));self.fields[name]=edit;form.addRow(label,edit)
        choose=Q.QPushButton('Choose background recording…');choose.clicked.connect(self.choose_background);form.addRow(choose)
        layout.addLayout(form)
        note=Q.QLabel('Linkage assumes unchanged counting geometry and stable background. Identify sensors and geometry explicitly; raw counts are never subtracted.')
        note.setWordWrap(True);layout.addWidget(note)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Save|Q.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
    def choose_background(self):
        folder=Q.QFileDialog.getExistingDirectory(self,'Background recording with manifest.json')
        if folder:self.fields['background_path'].setText(folder)


class ExperimentMixin:
    def _build_experiment_tab(self):
        page=Q.QWidget();layout=Q.QVBoxLayout(page);controls=Q.QHBoxLayout()
        self.sample_button=Q.QPushButton('Sample sheet…');self.sample_button.clicked.connect(self.edit_sample_sheet)
        self.monitor_button=Q.QPushButton('Monitoring / daily backup…');self.monitor_button.clicked.connect(self.configure_monitor)
        refresh=Q.QPushButton('Refresh QC');refresh.clicked.connect(lambda:self.request_science(True))
        export=Q.QPushButton('Export QC…');export.clicked.connect(self.export_science)
        self.science_net=Q.QCheckBox('Net rates');self.science_net.toggled.connect(self.draw_science)
        self.ingrowth_hours=Q.QDoubleSpinBox();self.ingrowth_hours.setRange(0,240);self.ingrowth_hours.setValue(3);self.ingrowth_hours.setSuffix(' h ingrowth exclusion')
        self.ingrowth_hours.valueChanged.connect(lambda:self.request_science(True))
        for widget in (self.sample_button,self.monitor_button,refresh,export,self.science_net,self.ingrowth_hours):controls.addWidget(widget)
        layout.addLayout(controls)
        self.experiment_overview=Q.QLabel('Open or start a recording to see experiment progress.')
        self.experiment_overview.setWordWrap(True);self.experiment_overview.setTextFormat(QtCore.Qt.TextFormat.PlainText);layout.addWidget(self.experiment_overview)
        self.science_figure=Figure(figsize=(10,5),facecolor=BG,layout='constrained')
        self.science_canvas=FigureCanvasQTAgg(self.science_figure);layout.addWidget(self.science_canvas,1)
        self.science_text=Q.QPlainTextEdit();self.science_text.setReadOnly(True);self.science_text.setMaximumHeight(125);layout.addWidget(self.science_text)
        note=Q.QLabel('Hourly native-channel sums. Peak and apparent decay fits are advisory, with conditional uncertainties. Device temperature is not canister temperature.')
        note.setWordWrap(True);layout.addWidget(note)
        self.tabs.addTab(page,'Experiment / scientific QC');self.experiment_page=page
        self.science_pool=QtCore.QThreadPool(self);self.science_pool.setMaxThreadCount(1)
        self.science_busy=False;self.science_pending=False;self.science_cancel=threading.Event();self.science_result=None;self._last_science=-float('inf')
        self.tabs.currentChanged.connect(lambda index:self.request_science() if self.tabs.widget(index) is page else None)
        browser=Q.QWidget();stack=Q.QVBoxLayout(browser);actions=Q.QHBoxLayout()
        scan=Q.QPushButton('Refresh recordings');scan.clicked.connect(self.scan_runs)
        open_view=Q.QPushButton('Open selected in another viewer');open_view.clicked.connect(self.open_listed_run)
        actions.addWidget(scan);actions.addWidget(open_view);stack.addLayout(actions)
        self.runs_table=Q.QTableWidget(0,4);self.runs_table.setHorizontalHeaderLabels(['Run label','Detector','Scheduled end UTC','Folder'])
        self.runs_table.setEditTriggers(Q.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.runs_table.horizontalHeader().setSectionResizeMode(3,Q.QHeaderView.ResizeMode.Stretch)
        stack.addWidget(self.runs_table)
        stack.addWidget(Q.QLabel('Read-only list. Another viewer keeps current acquisition and viewing independent.'))
        self.tabs.addTab(browser,'Recordings');self.viewer_windows=[];self.listed_runs=[]

    def refresh_experiment(self,state,active):
        current=annotations.seal(getattr(self,'notes',[]));summary=state.get('acquisition_summary') or {}
        monitor=read_monitor(self.run);sample=latest(self.run,'sample_sheet');now=self.current_timeline_seconds()
        normal=summary.get('normal_device_exposure_s');aggregate=summary.get('aggregate_device_exposure_s')
        lines=[self.run_summary.text(),
               f"{(now-current['utc'])/86400:.3f} d since seal" if current else 'Seal time not recorded',
               f"Device exposure: {summary.get('device_elapsed_s',0)/3600:.3f} h • measured counts: {summary.get('total_counts',0):,}",
               f"Normal / aggregate exposure: {normal/3600:.3f} / {aggregate/3600:.3f} h" if normal is not None else 'Detailed exposure breakdown loads with scientific QC',
               'Sample sheet: '+(f"version {sample['id']} • {sample['metadata']['sample_sheet'].get('sample_id','')}" if sample else 'not recorded'),
               'Quality flags: '+json.dumps(summary.get('quality_counts',{}))]
        last=monitor.get('last_backup')
        lines.append('Last verified backup: '+(f"{last['completed_utc']} • {last['snapshots']:,} spectra • {last['target']}" if last else 'none recorded by monitor'))
        lines.append('Monitoring: '+('running' if monitor and now-dt.datetime.fromisoformat(monitor['utc']).timestamp()<30 else 'not active / unavailable'))
        budget=disk_budget(self.run,state,self.run_settings or {},now)
        lines.append(f"Recording-volume free space: {budget['free_bytes']/1024**3:.2f} GiB • estimated data size: {budget['estimated_recording_bytes']/1024**3:.2f} GiB")
        if budget['estimated_remaining_bytes'] is not None:
            lines.append(f"Estimated additional raw storage to planned end: {budget['estimated_remaining_bytes']/1024**3:.2f} GiB; exports/backups require extra space.")
        if budget['headroom_warning']:lines.append('Storage headroom needs attention before the scheduled end.')
        # Schedule is already above the tabs. Keep the overview compact enough for useful plots.
        from analysis import midpoint
        endpoint=midpoint(state['last_snapshot']) if state.get('last_snapshot') else now
        lines=lines[2:]
        if current:lines.insert(0,f"{((now if active else endpoint)-current['utc'])/86400:.3f} d since seal • {'current timeline' if active else 'last saved endpoint'}")
        else:lines.insert(0,'Seal time not recorded')
        flags={k:v for k,v in summary.get('quality_counts',{}).items() if k not in ('normal','baseline')}
        lines=[line for line in lines if not line.startswith('Quality flags:')]
        if flags:lines.append('Review flags: '+json.dumps(flags))
        if monitor.get('active_conditions'):lines.append('Attention: '+'; '.join(monitor['active_conditions'].values()))
        self.experiment_overview.setText('\n'.join(lines))
        writable=not (self.run/'BACKUP_PLAN.json').exists() and not getattr(self,'read_only',False)
        self.sample_button.setEnabled(writable);self.monitor_button.setEnabled(writable)
        if self.tabs.currentWidget() is self.experiment_page:self.request_science()

    def request_science(self,force=False):
        if self._closing or not self.run or not (self.run/'run.sqlite').exists():return
        if self.science_busy:
            if force:self.science_pending=True;self.science_cancel.set()
            return
        if not force and time.monotonic()-self._last_science<60:return
        self._last_science=time.monotonic();self.science_busy=True;self.science_cancel=threading.Event()
        task=ScienceTask(self.run,self.ingrowth_hours.value(),self.science_cancel);self._science_task=task
        task.signals.done.connect(self.science_done);self.science_pool.start(task);set_text(self.science_text,'Loading verified scientific progress…')

    def science_done(self,message):
        self.science_busy=False
        if self.science_pending:
            self.science_pending=False;self.request_science(True);return
        if self._closing or str(self.run)!=message['run']:return
        if message['error']:set_text(self.science_text,'Scientific QC unavailable: '+message['error']);return
        if message['result'] is not None:self.science_result=message['result'];self.draw_science()

    def draw_science(self,*unused):
        result=self.science_result
        if not result:return
        rows=result['rows'];self.science_figure.clear();axes=self.science_figure.subplots(3,1)
        zero=result['seal_note']['utc'] if result['seal_note'] else rows[0]['start_utc'] if rows else 0
        x=np.array([(r['start_utc']+r['end_utc'])/2-zero for r in rows])/86400
        net=self.science_net.isChecked() and result['background'] is not None
        for name,color in zip(['Pb-214 352 region','609 / 583 region','K-40 region'],[TEAL,GOLD,'#9d9fff']):
            rate=[r['net_rate_cps'][name] if net and 'net_rate_cps' in r else r['window_counts'][name]/r['device_exposure_s'] for r in rows]
            error=[r['net_poisson_sigma_cps'][name] if net and 'net_poisson_sigma_cps' in r else np.sqrt(r['window_counts'][name])/r['device_exposure_s'] for r in rows]
            axes[0].errorbar(x,rate,yerr=error,color=color,label=name,linewidth=1,markersize=2,fmt='.-')
        axes[0].set_ylabel('Net cps' if net else 'Gross cps');axes[0].legend(fontsize=7,ncol=3,facecolor=BG,edgecolor=MUTED,labelcolor='#eef2f6')
        for name,color in [('609 region',GOLD),('K-40',TEAL)]:
            drift=[r.get('peaks',{}).get(name,{}).get('relative_drift_keV',np.nan) for r in rows]
            sigma=[r.get('peaks',{}).get(name,{}).get('centroid_sigma_keV',np.nan) for r in rows]
            axes[1].errorbar(x,drift,yerr=sigma,fmt='.-',color=color,label=name,markersize=2)
        axes[1].axhline(5,color=MUTED,linestyle='--');axes[1].axhline(-5,color=MUTED,linestyle='--')
        axes[1].set_ylabel('Shift (keV)');axes[1].legend(fontsize=7,ncol=2,facecolor=BG,edgecolor=MUTED,labelcolor='#eef2f6')
        axes[2].plot(x,[r.get('temperature_C') if r.get('temperature_C') is not None and r.get('temperature_quality')!='stale_or_projected_timestamp' else np.nan for r in rows],color=TEAL)
        axes[2].set_ylabel('Device temp (°C)');axes[2].set_xlabel('Days since seal' if result['seal_note'] else 'Days since first included interval')
        for ax in axes:
            ax.set_facecolor(BG);ax.tick_params(colors='#eef2f6');ax.xaxis.label.set_color('#eef2f6');ax.yaxis.label.set_color('#eef2f6');ax.grid(alpha=.15)
        self.science_canvas.draw_idle()
        text=[f"Through source spectrum {result['source_head']:,}. {result['cache_rule']}",
              'Device temperatures with timestamp age >300 s are omitted from the plot and flagged in export.',
              'No smoothing or raw subtraction. Net-rate errors share the linked-background variance across bins.',
              'Peak drift threshold: 5 keV from first qualified bin; weak fits are not forced.']
        if self.science_net.isChecked() and not result['background']:text.append('No linked background; showing gross rates.')
        for name,fit in result['decay_fits'].items():
            text.append(name+': '+(f"apparent half-life {fit['half_life_d']:.4f} ± {fit['half_life_sigma_d']:.4f} d (conditional model SE)" if fit['status']=='qualified_advisory' else fit['status']))
        if not result['seal_note']:text.append('No seal time: advisory decay fitting unavailable.')
        if rows:
            for name,peak in rows[-1].get('peaks',{}).items():
                text.append(name+': '+(f"centroid {peak['centroid_keV']:.2f} ± {peak['centroid_sigma_keV']:.2f} keV (conditional SE)" if peak.get('status')=='qualified_advisory' else peak.get('status','not available')))
        if result['background']:text.append(result['background']['assumption'])
        text.append('Ingrowth cutoff is an assumption; regions can contain interferences. No absolute activity or physical qualification.')
        set_text(self.science_text,'\n'.join(text))

    def edit_sample_sheet(self):
        if not self.run or getattr(self,'read_only',False):return
        old=latest(self.run,'sample_sheet');dialog=SampleDialog(self,old['metadata']['sample_sheet'] if old else {})
        while dialog.exec()==Q.QDialog.DialogCode.Accepted:
            try:
                utc,basis=self.note_clock();save_sheet(self.run,utc,{k:e.text() for k,e in dialog.fields.items()},basis)
                self.refresh_notes(True);self.request_science(True);break
            except (OSError,ValueError) as error:self.fail(error)

    def configure_monitor(self):
        if not self.run or getattr(self,'read_only',False):return
        dialog=Q.QDialog(self);dialog.setWindowTitle('Persistent monitoring');form=Q.QFormLayout(dialog);config=monitoring(self.run)
        enabled=Q.QCheckBox('Keep monitor active after window closes');enabled.setChecked(config['enabled'])
        notify=Q.QCheckBox('Notify on meaningful changes');notify.setChecked(config['notifications'])
        backup=Q.QLineEdit(config['backup_parent']);choose=Q.QPushButton('Choose daily backup parent…')
        def choose_folder():
            folder=Q.QFileDialog.getExistingDirectory(dialog,'Choose separate backup volume / folder',backup.text())
            if folder:backup.setText(folder)
        choose.clicked.connect(choose_folder)
        form.addRow(enabled);form.addRow(notify);form.addRow('Backup parent (blank disables)',backup);form.addRow(choose)
        note=Q.QLabel('Fresh snapshots every 24 h and at the end. Local alerts cannot run while the computer is off.');note.setWordWrap(True);form.addRow(note)
        buttons=Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Save|Q.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addRow(buttons)
        if dialog.exec()!=Q.QDialog.DialogCode.Accepted:return
        try:
            parent=Path(backup.text()).resolve() if backup.text().strip() else None
            if parent and (not parent.is_dir() or parent==self.run.resolve() or parent.is_relative_to(self.run.resolve())):raise ValueError('Choose an existing folder outside source recording')
            utc,basis=self.note_clock()
            annotations.add(self.run,utc,'monitoring','Monitoring preferences updated',basis,metadata={'monitoring':dict(
                enabled=enabled.isChecked(),notifications=notify.isChecked(),backup_parent=str(parent) if parent else '',backup_interval_s=86400,disconnect_warn_s=120)})
            self.refresh_notes(True)
            if enabled.isChecked():self.start_monitor()
        except (OSError,ValueError) as error:self.fail(error)

    def start_monitor(self):
        if not self.run:return
        import os,subprocess
        from runtime import python_command
        if os.environ.get('GAMMAKEEPER_TEST_MODE')=='1':return
        subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'monitor.py'),str(self.run)],cwd=ROOT,
            creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)

    def export_science(self):
        if not self.run:return
        parent=Q.QFileDialog.getExistingDirectory(self,'Save a versioned QC export under',str(self.run))
        if parent:
            target=Path(parent)/('scientific_qc_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
            self.run_job('scientific_qc.py',target,'--target',str(target),'--ingrowth-exclusion-hours',str(self.ingrowth_hours.value()))

    def scan_runs(self):
        self.listed_runs=[]
        import os
        roots=[Path(self.folder.text()).resolve()]
        if os.environ.get('GAMMAKEEPER_TEST_MODE')!='1' or os.environ.get('GAMMAKEEPER_DATA_ROOT'):roots.append(DATA_ROOT)
        for root in dict.fromkeys(roots):
            for file in sorted(root.glob('*/manifest.json'),reverse=True):
                try:
                    json.loads(file.read_text(encoding='utf-8'))
                    if file.parent not in self.listed_runs:self.listed_runs.append(file.parent)
                except (OSError,ValueError):continue
        self.runs_table.setRowCount(len(self.listed_runs))
        for i,run in enumerate(self.listed_runs):
            config=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
            for j,value in enumerate([config.get('label',''),config.get('serial',''),config.get('planned_end_utc') or 'continuous',str(run)]):
                self.runs_table.setItem(i,j,Q.QTableWidgetItem(value))

    def open_listed_run(self):
        row=self.runs_table.currentRow()
        if row>=0:
            from ui.main_window import Window
            viewer=Window(str(self.listed_runs[row]),read_only=True);self.viewer_windows.append(viewer);viewer.show()
