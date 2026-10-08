"""Owned GUI analysis, background previews and detached audit/backup jobs."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
import uuid
import hashlib
import numpy as np
from PySide6 import QtCore,QtGui,QtWidgets as Q
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg,NavigationToolbar2QT
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
from export_run import readonly
from preview import read_preview
from runtime import python_command
from run_policy import resume_allowed
from system_time import boot_identity
from spectrum_view import latest_interval


class PreviewSignals(QtCore.QObject):
    done=QtCore.Signal(object)
    progress=QtCore.Signal(str)


class PreviewTask(QtCore.QRunnable):
    def __init__(self,run,view,epoch,key,cancel,cache):
        super().__init__()
        self.signals=PreviewSignals()
        self.run_path,self.view,self.epoch,self.key,self.cancel,self.cache=run,view,epoch,key,cancel,cache

    def run(self):
        try:
            result=read_preview(self.run_path,self.view,self.epoch,progress=self.signals.progress.emit,
                                cancelled=self.cancel.is_set,cache_dir=self.cache)
            message=dict(key=self.key,result=result,error=None)
        except Exception as error:
            message=dict(key=self.key,result=None,error=f'{type(error).__name__}: {error}')
        self.signals.done.emit(message)


class SpectrumCanvas(FigureCanvasQTAgg):
    def __init__(self,bg,panel,text):
        self.figure=Figure(figsize=(10,6),facecolor=bg,layout='constrained')
        super().__init__(self.figure)
        self.ax=self.figure.add_subplot(111)
        self.panel,self.text=panel,text
        self.last_counts=None
        self.last_roi_counts=None
        self.empty('Waiting for a recorded interval. The first spectrum is a baseline.')

    def style(self):
        self.ax.set_facecolor(self.panel)
        self.ax.tick_params(colors=self.text)
        for spine in self.ax.spines.values():spine.set_color(self.text)
        self.ax.xaxis.label.set_color(self.text);self.ax.yaxis.label.set_color(self.text)

    def empty(self,message):
        self.ax.clear();self.style();self.last_counts=None;self.last_roi_counts=None
        self.ax.text(.5,.5,message,ha='center',va='center',color=self.text,transform=self.ax.transAxes,wrap=True)
        self.ax.set_xlabel('Energy (keV) • original channel calibration')
        self.ax.set_ylabel('Recorded interval counts / channel')
        self.draw_idle()

    def show_interval(self,row,roi=None):
        utc,epoch,d,s=row
        self.ax.clear();self.style();self.last_counts=None;self.last_roi_counts=None
        counts=d.get('counts_delta')
        if counts is None:
            self.ax.text(.5,.5,'No independent spectrum for this boundary',ha='center',color=self.text,transform=self.ax.transAxes)
        else:
            self.last_counts=list(counts)
            channels=np.arange(len(counts));a0,a1,a2=s['spectrum']['calibration_keV']
            energy=a0+a1*channels+a2*channels**2
            self.ax.step(energy[:-1],counts[:-1],where='mid',color='#43d8bf',linewidth=1.)
            self.ax.plot(energy[-1],counts[-1],'x',color='#f5bc66',label='Terminal channel • overflow interpretation requires confirmation')
            if roi:
                mask=(energy>=roi[0])&(energy<=roi[1]);mask[-1]=mask[-1] and roi[2]
                selected=int(np.asarray(counts)[mask].sum())
                self.last_roi_counts=selected
                self.ax.axvspan(roi[0],roi[1],color='#f5bc66',alpha=.15)
                self.ax.text(.02,.96,f'ROI: {selected:,} counts',va='top',transform=self.ax.transAxes,color=self.text)
            self.ax.legend(facecolor=self.panel,labelcolor=self.text,fontsize=8)
        self.ax.set_xlabel('Energy (keV) • original channel calibration',color=self.text)
        self.ax.set_ylabel('Recorded interval counts / channel',color=self.text)
        end=dt.datetime.fromtimestamp(utc,dt.timezone.utc).isoformat(timespec='seconds')
        self.ax.set_title(f'{end} • epoch {epoch} • '+', '.join(d['quality']),color=self.text,fontsize=10)
        self.draw_idle()


class AnalysisUI:
    def current_timeline_seconds(self):
        clock=((self.last_status or {}).get('last_snapshot') or {}).get('clock') or {}
        if clock.get('anchor_monotonic_s') is not None and clock.get('system_boot_id') is not None and clock['system_boot_id']==boot_identity():
            elapsed=time.perf_counter()-clock['anchor_monotonic_s']
            if elapsed>=0:
                rate=.005
                correction=max(-elapsed*rate,min(clock.get('slew_total_s',0.),elapsed*rate))
                return clock['anchor_utc_s']+elapsed+correction
        return dt.datetime.now(dt.timezone.utc).timestamp()

    def init_analysis(self,right,root,bg,panel,text):
        self.analysis_root=root
        self.preview_pool=QtCore.QThreadPool(self);self.preview_pool.setMaxThreadCount(1)
        self.preview_cancel=threading.Event();self.preview_busy=False;self.preview_pending=False
        self.preview_key=None;self.analysis_rows=[];self.preview_summary={};self._closing=False
        self.background_jobs=[];self._last_events=0;self._last_disk=0
        self.selected_analysis_row=None
        self.tabs=Q.QTabWidget();page=Q.QWidget();layout=Q.QVBoxLayout(page);layout.setContentsMargins(0,0,0,0)
        controls=Q.QHBoxLayout()
        self.view_mode=Q.QComboBox();self.view_mode.addItems(['Recent intervals','Whole run','Selected UTC window'])
        self.view_epoch=Q.QComboBox();self.view_epoch.addItem('All epochs',None)
        self.follow_live=Q.QCheckBox('Follow live');self.follow_live.setChecked(True)
        self.axis_timezone=Q.QComboBox();self.axis_timezone.addItems(['UTC','Local time'])
        self.color_percentile=Q.QDoubleSpinBox();self.color_percentile.setRange(90,100);self.color_percentile.setDecimals(1);self.color_percentile.setValue(99.5);self.color_percentile.setSuffix(' % color limit')
        refresh=Q.QPushButton('Refresh plot');refresh.clicked.connect(lambda:self.request_preview(True))
        for widget in (self.view_mode,self.view_epoch,self.follow_live,self.axis_timezone,self.color_percentile,refresh):controls.addWidget(widget)
        layout.addLayout(controls)
        ranges=Q.QHBoxLayout()
        self.view_from=Q.QDateTimeEdit();self.view_until=Q.QDateTimeEdit()
        for edit in (self.view_from,self.view_until):
            edit.setDisplayFormat('yyyy-MM-dd HH:mm:ss');edit.setCalendarPopup(True);edit.setTimeZone(QtCore.QTimeZone(0))
        ranges.addWidget(Q.QLabel('UTC from'));ranges.addWidget(self.view_from)
        ranges.addWidget(Q.QLabel('to'));ranges.addWidget(self.view_until)
        self.roi_enabled=Q.QCheckBox('ROI')
        self.roi_from=Q.QDoubleSpinBox();self.roi_to=Q.QDoubleSpinBox()
        for spin in (self.roi_from,self.roi_to):spin.setRange(0,10000);spin.setDecimals(1);spin.setSuffix(' keV')
        self.roi_from.setValue(500);self.roi_to.setValue(700)
        self.roi_terminal=Q.QCheckBox('Include terminal channel')
        for widget in (self.roi_enabled,self.roi_from,self.roi_to,self.roi_terminal):ranges.addWidget(widget)
        layout.addLayout(ranges)
        self.preview_note=Q.QLabel('Plots are derived views; original records remain saved. Click the heatmap to inspect an interval spectrum.')
        self.preview_note.setWordWrap(True);self.preview_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.preview_note)
        layout.addWidget(self.chart_toolbar);layout.addWidget(self.charts,1)
        self.tabs.addTab(page,'Time series')
        spectrum_page=Q.QWidget();spectrum_layout=Q.QVBoxLayout(spectrum_page)
        spectrum_controls=Q.QHBoxLayout()
        self.spectrum_mode=Q.QComboBox();self.spectrum_mode.addItems(['Latest recorded interval','Selected heatmap interval / group'])
        self.spectrum_refresh=Q.QPushButton('Refresh spectrum')
        spectrum_controls.addWidget(Q.QLabel('Spectrum source'));spectrum_controls.addWidget(self.spectrum_mode,1)
        spectrum_controls.addWidget(self.spectrum_refresh)
        spectrum_layout.addLayout(spectrum_controls)
        spectrum_roi=Q.QHBoxLayout()
        self.spectrum_roi_enabled=Q.QCheckBox('ROI');self.spectrum_roi_enabled.setChecked(self.roi_enabled.isChecked())
        self.spectrum_roi_from=Q.QDoubleSpinBox();self.spectrum_roi_to=Q.QDoubleSpinBox()
        for control in (self.spectrum_roi_from,self.spectrum_roi_to):control.setRange(0,10000);control.setDecimals(1);control.setSuffix(' keV')
        self.spectrum_roi_from.setValue(self.roi_from.value());self.spectrum_roi_to.setValue(self.roi_to.value())
        self.spectrum_roi_terminal=Q.QCheckBox('Include terminal channel')
        for widget in (self.spectrum_roi_enabled,self.spectrum_roi_from,self.spectrum_roi_to,self.spectrum_roi_terminal):spectrum_roi.addWidget(widget)
        spectrum_roi.addStretch();spectrum_layout.addLayout(spectrum_roi)
        self.spectrum_note=Q.QLabel('Latest interval updates while this tab is open. Click a heatmap interval to keep that selection.')
        self.spectrum_note.setWordWrap(True);self.spectrum_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        spectrum_layout.addWidget(self.spectrum_note)
        self.spectrum_canvas=SpectrumCanvas(bg,panel,text)
        spectrum_layout.addWidget(NavigationToolbar2QT(self.spectrum_canvas,self));spectrum_layout.addWidget(self.spectrum_canvas)
        self.tabs.addTab(spectrum_page,'Spectrum / ROI')
        self._spectrum_key=None
        self.summary_view=Q.QPlainTextEdit();self.summary_view.setReadOnly(True)
        self.tabs.addTab(self.summary_view,'Run summary')
        event_page=Q.QWidget();event_layout=Q.QVBoxLayout(event_page)
        self.event_search=Q.QLineEdit();self.event_search.setPlaceholderText('Filter events, warnings or errors')
        self.event_search.textChanged.connect(lambda:self.refresh_events(True))
        event_layout.addWidget(self.event_search)
        self.event_table=Q.QTableWidget(0,4);self.event_table.setHorizontalHeaderLabels(['UTC','Source','Event','Details'])
        self.event_table.setEditTriggers(Q.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.horizontalHeader().setSectionResizeMode(3,Q.QHeaderView.ResizeMode.Stretch)
        event_layout.addWidget(self.event_table)
        logs=Q.QPushButton('Open recorder diagnostics');logs.clicked.connect(self.open_diagnostics);event_layout.addWidget(logs)
        self.tabs.addTab(event_page,'Events / logs')
        right.addWidget(self.tabs,1)
        self.charts.mpl_connect('button_press_event',self.select_interval)
        self.charts.mpl_connect('button_release_event',self.navigation_finished)
        self.view_mode.currentIndexChanged.connect(lambda:self.request_preview(True))
        self.view_epoch.currentIndexChanged.connect(lambda:self.request_preview(True))
        for control in (self.axis_timezone,self.color_percentile,self.roi_from,self.roi_to):
            signal=control.currentIndexChanged if isinstance(control,Q.QComboBox) else control.valueChanged
            signal.connect(self.redraw_analysis)
        self.roi_enabled.toggled.connect(self.redraw_analysis);self.roi_terminal.toggled.connect(self.redraw_analysis)
        self.follow_live.toggled.connect(lambda checked:self.request_preview(True) if checked else None)
        self.tabs.currentChanged.connect(self.spectrum_tab_changed)
        self.spectrum_mode.currentIndexChanged.connect(lambda:self.refresh_spectrum(True))
        self.spectrum_refresh.clicked.connect(lambda:self.refresh_spectrum(True))
        # Both tabs edit the same ROI; the controls remain usable in either view.
        for original,mirror in ((self.roi_from,self.spectrum_roi_from),(self.roi_to,self.spectrum_roi_to)):
            original.valueChanged.connect(mirror.setValue);mirror.valueChanged.connect(original.setValue)
        for original,mirror in ((self.roi_enabled,self.spectrum_roi_enabled),(self.roi_terminal,self.spectrum_roi_terminal)):
            original.toggled.connect(mirror.setChecked);mirror.toggled.connect(original.setChecked)
        self.view_from.setToolTip('Select saved intervals whose end timestamps lie in this UTC window; counts are not redistributed.')
        self.view_until.setToolTip(self.view_from.toolTip())

    def request_preview(self,force=False):
        if not self.run or not (self.run/'run.sqlite').exists() or self._closing:
            return
        if not force and not self.follow_live.isChecked():return
        if self.preview_busy:
            if force:
                self.preview_cancel.set();self.preview_pending=True
            return
        try:
            view=('recent','full',None)[self.view_mode.currentIndex()]
            db=readonly(self.run/'run.sqlite')
            try:
                head=db.execute('SELECT coalesce(max(seq),-1) FROM snapshots').fetchone()[0]
                epochs=[x[0] for x in db.execute('SELECT DISTINCT epoch FROM snapshots ORDER BY epoch')]
                if self.view_mode.currentIndex()==2:
                    begin=self.view_from.dateTime().toSecsSinceEpoch();end=self.view_until.dateTime().toSecsSinceEpoch()
                    if begin>=end:raise ValueError('The selected UTC start must precede its end')
                    low,high=db.execute('SELECT min(seq),max(seq) FROM snapshots WHERE utc>=? AND utc<=?',(begin,end)).fetchone()
                    if low is None:raise ValueError('No saved intervals in the selected UTC window')
                    view=(low,high)
                if self.view_epoch.count()!=len(epochs)+1:
                    current=self.view_epoch.currentData();self.view_epoch.blockSignals(True)
                    self.view_epoch.clear();self.view_epoch.addItem('All epochs',None)
                    for epoch in epochs:self.view_epoch.addItem(f'Epoch {epoch}',epoch)
                    self.view_epoch.setCurrentIndex(max(0,self.view_epoch.findData(current)));self.view_epoch.blockSignals(False)
            finally:db.close()
            if head<0:return
            key=(str(self.run.resolve()),repr(view),self.view_epoch.currentData(),head)
            if key==self.preview_key and not force:return
            self.preview_cancel=threading.Event();self.preview_busy=True
            self._preview_forced=force
            task=PreviewTask(self.run,view,self.view_epoch.currentData(),key,self.preview_cancel,self.analysis_root/'cache'/'previews')
            self._preview_task=task
            task.signals.progress.connect(self.preview_note.setText)
            task.signals.done.connect(self.preview_done)
            self.preview_pool.start(task)
        except (OSError,ValueError,sqlite3.Error) as error:
            self.preview_note.setText(str(error))

    def preview_done(self,message):
        self.preview_busy=False
        if self._closing:return
        if self.preview_pending:
            self.preview_pending=False;self.request_preview(True);return
        if not self.run or message['key'][0]!=str(self.run.resolve()):return
        if message['error']:
            self.preview_note.setText('Plot unavailable: '+message['error']);return
        result=message['result']
        if result is None:return
        self.preview_key=message['key'];self.analysis_rows=result['rows'];self.preview_summary=result['summary']
        self.charts.display_label=f"{len(self.analysis_rows)} displayed intervals • {result['aggregation']} recorded intervals / group"
        kind='Cached preview' if result.get('cached') else 'Verified raw reconstruction'
        self.preview_note.setText(f"{kind} through spectrum {result['head']:,}; source check {result['source_verified_utc']}. "
                                  'Groups sum counts / device seconds; gaps remain blank. Full integrity audit is separate. Click a heatmap interval to inspect its spectrum.')
        if self.follow_live.isChecked() or self._preview_forced:self.redraw_analysis()

    def redraw_analysis(self,*args):
        self.refresh_spectrum(True)
        if not self.analysis_rows:return
        snapshot=self.last_status.get('last_snapshot') or self.analysis_rows[-1][3]
        self.charts.color_percentile=self.color_percentile.value()
        self.charts.local_time=self.axis_timezone.currentIndex()==1
        self.charts.show_dates=self.view_mode.currentIndex()!=0
        self.charts.roi=(self.roi_from.value(),self.roi_to.value(),self.roi_terminal.isChecked()) if self.roi_enabled.isChecked() and self.roi_to.value()>self.roi_from.value() else None
        self.charts.show_rows(self.analysis_rows,snapshot,self.last_status.get('mode','device'))

    def select_interval(self,event):
        if event.inaxes!=self.charts.spg or event.ydata is None or not self.analysis_rows or self.chart_toolbar.mode:return
        ends=np.array([mdates.date2num(dt.datetime.fromtimestamp(row[0],dt.timezone.utc)) for row in self.analysis_rows])
        index=min(len(ends)-1,int(np.searchsorted(ends,event.ydata)))
        self.selected_analysis_row=self.analysis_rows[index]
        self.spectrum_mode.setCurrentIndex(1)
        self.refresh_spectrum(True)
        self.tabs.setCurrentIndex(1)

    def spectrum_tab_changed(self,index):
        if index==1:self.refresh_spectrum(True)

    def refresh_spectrum(self,force=False):
        if not hasattr(self,'spectrum_canvas') or self._closing:return
        if self.tabs.currentIndex()!=1 and not force:return
        if not self.run or not (self.run/'run.sqlite').exists():
            self.spectrum_canvas.empty('Open a recording or start one to view its interval spectra.')
            self.spectrum_note.setText('No recording selected.');return
        roi=(self.roi_from.value(),self.roi_to.value(),self.roi_terminal.isChecked()) if self.roi_enabled.isChecked() else None
        if roi and roi[1]<=roi[0]:
            self.spectrum_note.setText('ROI upper energy must exceed lower energy.');return
        try:
            if self.spectrum_mode.currentIndex()==0:
                row=latest_interval(self.run,self.view_epoch.currentData())
                label='Latest recorded interval'
            else:
                row=self.selected_analysis_row;label='Selected heatmap interval / group'
            if row is None:
                self.spectrum_canvas.empty('Waiting for a recorded interval.' if self.spectrum_mode.currentIndex()==0 else 'Select an interval on the time-series heatmap, or choose Latest recorded interval.')
                self.spectrum_note.setText(label+' • no interval available yet');return
            key=(str(self.run.resolve()),self.spectrum_mode.currentIndex(),row[0],row[1],repr(roi),self.view_epoch.currentData())
            if key==self._spectrum_key and not force:return
            self.spectrum_canvas.show_interval(row,roi);self._spectrum_key=key
            d=row[2];counts=d.get('counts_delta')
            total=sum(counts) if counts is not None else None
            text=f'{label} • epoch {row[1]} • '+', '.join(d['quality'])
            if total is not None:text+=f" • {total:,} counts • device duration {d['device_duration_s']:g} s"
            else:text+=' • baseline only; waiting for the next spectrum difference'
            if roi:
                selected=self.spectrum_canvas.last_roi_counts
                text+=f' • ROI {selected:,} counts' if selected is not None else ''
            self.spectrum_note.setText(text)
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
            self.spectrum_canvas.empty('Spectrum unavailable; see the message above.')
            self.spectrum_note.setText(f'Spectrum unavailable: {error}')

    def navigation_finished(self,event):
        if self.chart_toolbar.mode:self.follow_live.setChecked(False)

    def refresh_events(self,force=False):
        if not self.run or (not force and time.monotonic()-self._last_events<5):return
        self._last_events=time.monotonic();events=[]
        for source,name in (('recorder','run.sqlite'),('supervisor','supervisor.sqlite')):
            if not (self.run/name).exists():continue
            db=readonly(self.run/name)
            try:
                events.extend((utc,source,kind,payload) for utc,kind,payload in db.execute('SELECT utc,kind,payload FROM events ORDER BY id DESC LIMIT 250'))
            finally:db.close()
        query=self.event_search.text().casefold()
        events=sorted(events,reverse=True)
        events=[row for row in events if query in ' '.join(row).casefold()][:300]
        self.event_table.setRowCount(len(events))
        for i,row in enumerate(events):
            for j,value in enumerate(row):
                item=Q.QTableWidgetItem(value if j!=3 else value[:300]);item.setToolTip(value)
                self.event_table.setItem(i,j,item)

    def open_diagnostics(self):
        path=(self.last_status.get('supervisor') or {}).get('child_log') or self.last_launch_log
        if path:QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))

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

    def refresh_analysis_status(self,state,active,coverage):
        self.refresh_spectrum()
        for job in list(self.background_jobs):
            if job['process'].poll() is not None:
                self.message(f"{job['script']}: {'complete' if job['process'].returncode==0 else 'failed; inspect log '+str(job['log'])}: {job['target']}")
                self.background_jobs.remove(job)
        summary=state.get('acquisition_summary') or (self.preview_summary if self.view_mode.currentIndex()==1 else {})
        if not summary and state.get('last_snapshot') and (state.get('last_interval') or {}).get('epoch',0)==0:
            db=readonly(self.run/'run.sqlite')
            try:
                first=db.execute('SELECT file,sha256 FROM snapshots ORDER BY seq LIMIT 1').fetchone()
            finally:db.close()
            if first:
                payload=(self.run/first[0]).read_bytes()
                if hashlib.sha256(payload).hexdigest()!=first[1]:raise ValueError('Initial spectrum integrity mismatch')
                initial=json.loads(payload);latest=state['last_snapshot']
                start=initial['spectrum'];end=latest['spectrum'];duration=end['duration_s']-start['duration_s']
                if len(start['counts'])==len(end['counts']) and start['calibration_keV']==end['calibration_keV'] and duration>0:
                    counts=sum(end['counts'])-sum(start['counts'])
                    summary=dict(total_counts=counts,device_elapsed_s=duration,mean_spectral_rate_cps=counts/duration,
                                 quality_counts={'source':'endpoint difference; full flags in whole-run view/audit'})
        rows=['RUN SUMMARY',self.run_summary.text(),'']
        if summary:
            counts=summary.get('total_counts',0);duration=summary.get('device_elapsed_s',0)
            rate=summary.get('mean_spectral_rate_cps')
            rows += [f'Measured interval counts: {counts:,}',f'Summed device duration: {duration:.3f} s',
                     f'Mean spectral rate: {rate:.6f} cps' if rate is not None else 'Mean spectral rate: waiting',
                     'Counting-only Poisson sigma: '+(f'{np.sqrt(counts)/duration:.6f} cps' if duration else 'unavailable'),
                     'Quality flags: '+json.dumps(summary.get('quality_counts',{})), '']
            if not active and state['state']=='completed' and rate is not None:
                self.metrics[0].setText(f'{rate:.2f} cps');self.metric_captions[0].setText('Spectrum rate • whole run')
        rows += [f"Saved spectra: {state.get('snapshots',0):,}",f'Recomputed dose time coverage: {coverage:.5%}' if coverage is not None else 'Loading saved telemetry; final coverage not yet available.',
                 'Dose time basis: received estimator observations; ≤3 s zero-order hold; sample/filter latency uncalibrated.',
                 'Spectral rate uses actual device duration; one-second device duration quantization is retained.',
                 'Terminal channel is retained in totals; its overflow semantics require confirmation before peak interpretation.','']
        snapshot=state.get('last_snapshot') or {}
        counter=snapshot.get('accumulated_dose') or {}
        if counter:
            from analysis import midpoint,timestamp
            age=midpoint(snapshot)-timestamp(counter['dt'])
            rows += [f"Native accumulated-dose difference: {snapshot.get('device_experiment_dose_uSv',0):.5f} µSv",
                     f'Native counter record age at spectrum endpoint: {age:.1f} s',
                     'This older counter is separate from the received-estimator integral.','']
        target=(state.get('supervisor') or {}).get('completion_target')
        if not target:
            candidates=sorted(self.run.glob('completion_*/VALIDATION.json'))
            target=str(candidates[-1].parent) if candidates else None
        if target:
            file=Path(target)/'VALIDATION.json'
            if file.exists():
                verdict=json.loads(file.read_text(encoding='utf-8'))
                rows += ['INTEGRITY VALIDATION: '+('PASSED' if verdict.get('passed') else 'FAILED'),
                         'Data quality: '+verdict.get('data_quality','review report'),
                         'Report: '+str(file)]
                if not verdict.get('passed'):rows.append(verdict.get('error',''))
            else:rows += ['Completion audit/export: running in background; capture status is separate.']
        else:rows += ['Completion audit/export: not yet run.']
        if time.monotonic()-self._last_disk>10:
            import shutil
            self._last_disk=time.monotonic();self._disk_free=shutil.disk_usage(self.run).free
        free=getattr(self,'_disk_free',0)
        rows += ['',f'Free space on recording volume: {free/1024**3:.2f} GiB',
                 'Recording stops below 256 MiB free; reserve space for exports and backups.']
        self.summary_view.setPlainText('\n'.join(rows))
        self.audit_button.setEnabled(state['state']=='completed' and not any(j['script']=='completion.py' for j in self.background_jobs))
        self.refresh_events()
