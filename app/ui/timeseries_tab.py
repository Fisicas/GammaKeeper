"""Time series tab: background previews, heatmap selection and display scales."""
import datetime as dt
import sqlite3
import threading
import time

import numpy as np
from PySide6 import QtCore, QtWidgets as Q
import matplotlib.dates as mdates
from export_run import readonly
from plot_scales import apply_scale
import display_controls
from ui.tasks import PreviewTask
from ui.theme import TEAL, GOLD, MUTED

# Time-series windows: seconds before the latest saved spectrum, or a named mode.
VIEW_WINDOWS = [('Last 5 minutes', 300), ('Last 30 minutes', 1800), ('Last 1 hour', 3600), ('Last 6 hours', 21600),
                ('Last 24 hours', 86400), ('Last 240 intervals', 'recent'), ('Whole run', 'full'),
                ('Selected UTC window', 'window')]
DEFAULT_WINDOW = 1800


class TimeSeriesMixin:
    def _build_timeseries_tab(self):
        """Plot-first live dashboard; less-used plotting controls are collapsed."""
        page=Q.QWidget();layout=Q.QVBoxLayout(page);layout.setContentsMargins(0,0,0,0)
        actions=Q.QHBoxLayout()
        self.plot_options_button=Q.QToolButton();self.plot_options_button.setText('Plot options ▸')
        self.plot_options_button.setCheckable(True);self.plot_options_button.setChecked(False)
        self.plot_options=Q.QFrame();self.plot_options.setObjectName('panel')
        options_layout=Q.QVBoxLayout(self.plot_options);options_layout.setContentsMargins(8,8,8,8)
        self.plot_refresh=Q.QPushButton('Refresh plots');self.plot_refresh.clicked.connect(lambda:self.request_preview(True))
        actions.addWidget(self.plot_options_button);actions.addStretch();actions.addWidget(self.plot_refresh)
        layout.addLayout(actions)
        layout.addWidget(self.plot_options)
        self.plot_options.setVisible(False)
        self.plot_options_button.toggled.connect(lambda shown:(self.plot_options.setVisible(shown),
                                                               self.plot_options_button.setText('Plot options ▾' if shown else 'Plot options ▸')))
        controls=Q.QHBoxLayout()
        self.view_mode=Q.QComboBox()
        for text,data in VIEW_WINDOWS:self.view_mode.addItem(text,data)
        self.view_mode.setCurrentIndex(self.view_mode.findData(DEFAULT_WINDOW))
        self.view_mode.setToolTip('Time span of the spectrogram, count-rate and dose-rate plots. Windows are measured back from the latest saved spectrum; long spans group intervals (counts summed, divided by device seconds).')
        self.view_epoch=Q.QComboBox();self.view_epoch.addItem('All epochs',None)
        self.follow_live=Q.QCheckBox('Follow live');self.follow_live.setChecked(True)
        self.axis_timezone=Q.QComboBox();self.axis_timezone.addItems(['UTC','Local time'])
        self.color_percentile=Q.QDoubleSpinBox();self.color_percentile.setRange(90,100);self.color_percentile.setDecimals(1);self.color_percentile.setValue(99.5);self.color_percentile.setSuffix(' % color limit')
        for widget in (self.view_mode,self.view_epoch,self.follow_live,self.axis_timezone,self.color_percentile):controls.addWidget(widget)
        options_layout.addLayout(controls)
        options_layout.addLayout(display_controls.controls(self))
        self.display_scale_note=Q.QLabel('Scales change display only; log axes hide zeros/nonpositive values. Spectrogram intensity is independent of the count-rate axis.')
        self.display_scale_note.setWordWrap(True)
        self.display_scale_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        options_layout.addWidget(self.display_scale_note)
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
        options_layout.addLayout(ranges)
        self.preview_note=Q.QLabel('Plots are derived views; original records remain saved. Click the heatmap to inspect an interval spectrum.')
        self.preview_note.setWordWrap(True);self.preview_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        options_layout.addWidget(self.preview_note)
        self.plot_cadence=Q.QLabel('Acquisition interval: waiting for run settings · displayed grouping: waiting for plot data')
        self.plot_cadence.setObjectName('muted')
        layout.addWidget(self.plot_cadence)
        legend=Q.QHBoxLayout();legend.setSpacing(18)
        self.series_buttons={}
        for key,label,color,line_style in (
                ('counts','Spectrum differences · cps',TEAL,'━'),
                ('estimator','Device estimator · cps',MUTED,'┄'),
                ('dose','Device dose rate · µSv/h',GOLD,'━')):
            button=Q.QToolButton();button.setText(f'{line_style}  {label}');button.setCheckable(True);button.setChecked(True)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextOnly)
            button.setStyleSheet(f'QToolButton {{ color: {color}; background: transparent; font-weight: 500; padding: 2px; }} QToolButton:!checked {{ color: {MUTED}; }}')
            button.toggled.connect(lambda checked,name=key:self.charts.set_series_visible(name,checked))
            self.series_buttons[key]=button;legend.addWidget(button)
        legend.addStretch();layout.addLayout(legend)
        layout.addWidget(self.chart_toolbar);layout.addWidget(self.charts,1)
        self.tabs.addTab(page,'Live dashboard')

    def request_preview(self,force=False):
        if not self.run or not (self.run/'run.sqlite').exists() or self._closing:
            return
        if not force and not self.follow_live.isChecked():return
        if not force and self.view_mode.currentData()=='full' and time.monotonic()-getattr(self,'_last_preview_request',-float('inf'))<60:
            return
        if self.preview_busy:
            if force:
                self.preview_cancel.set();self.preview_pending=True
                self.update_plot_cadence()
            return
        try:
            mode=self.view_mode.currentData()
            view=mode if mode in ('recent','full') else None
            db=readonly(self.run/'run.sqlite')
            try:
                head=db.execute('SELECT coalesce(max(seq),-1) FROM snapshots').fetchone()[0]
                epochs=[x[0] for x in db.execute('SELECT DISTINCT epoch FROM snapshots ORDER BY epoch')]
                if isinstance(mode,int):
                    latest=db.execute('SELECT max(utc) FROM snapshots').fetchone()[0]
                    if latest is not None:
                        low,high=db.execute('SELECT min(seq),max(seq) FROM snapshots WHERE utc>=?',(latest-mode,)).fetchone()
                        view=(low,high)
                elif mode=='window':
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
            self.update_plot_cadence()
            self._last_preview_request=time.monotonic()
            self._preview_forced=force
            task=PreviewTask(self.run,view,self.view_epoch.currentData(),key,self.preview_cancel,self.analysis_root/'cache'/'previews')
            self._preview_task=task
            task.signals.progress.connect(self.preview_note.setText)
            task.signals.done.connect(self.preview_done)
            self.preview_pool.start(task)
        except (OSError,ValueError,sqlite3.Error) as error:
            self.preview_note.setText(str(error))

    def update_plot_cadence(self, interval_s=None):
        """Keep acquisition cadence distinct from display grouping during refreshes."""
        if interval_s is None:
            interval_s=float((self.last_status or {}).get('interval_s',10))
        if self.preview_busy or self.preview_pending:
            grouping='updating preview'
        elif self.preview_aggregation is None:
            grouping='waiting for plot data'
        elif self.preview_aggregation==1:
            grouping='individual recorded intervals'
        else:
            grouping=f'up to {self.preview_aggregation} recorded intervals per plotted group'
        self.plot_cadence.setText(f'Acquisition interval: {interval_s:g} s  ·  Display: {grouping}')

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
        self.preview_aggregation=int(result['aggregation'])
        self.charts.display_label=f"{len(self.analysis_rows)} displayed intervals • up to {result['aggregation']} recorded intervals / group"
        self.update_plot_cadence()
        kind='Cached prefix + verified new intervals' if result.get('cached') else 'Verified raw reconstruction'
        self.preview_note.setText(f"{kind} through spectrum {result['head']:,}; source check {result['source_verified_utc']}. "
                                  'Groups sum counts / device seconds; gaps remain blank. Full integrity audit is separate. Click a heatmap interval to inspect its spectrum.')
        if self.follow_live.isChecked() or self._preview_forced:self.redraw_analysis()

    def redraw_analysis(self,*args):
        self.refresh_spectrum(True)
        if not self.analysis_rows:return
        snapshot=self.last_status.get('last_snapshot') or self.analysis_rows[-1][3]
        self.charts.color_percentile=self.color_percentile.value()
        self.charts.local_time=self.axis_timezone.currentIndex()==1
        self.charts.show_dates=self.view_mode.currentData() in ('full','window') or self.view_mode.currentData()==86400
        self.charts.roi=(self.roi_from.value(),self.roi_to.value(),self.roi_terminal.isChecked()) if self.roi_enabled.isChecked() and self.roi_to.value()>self.roi_from.value() else None
        self.charts.show_rows(self.analysis_rows,snapshot,self.last_status.get('mode','device'))

    def display_scales_changed(self,*unused):
        if not hasattr(self,'spectrum_canvas'):return
        energy=self.energy_axis.currentData();counts=self.counts_axis.currentData()
        self.spectrum_canvas.energy_scale=energy;self.spectrum_canvas.counts_scale=counts
        self.charts.energy_scale=energy;self.charts.counts_scale=counts
        self.charts.intensity_scale=self.intensity_scale.currentData()
        self.charts.colormap=self.spectrogram_colormap.currentData()
        # Redraw the already displayed row; toggling a scale never starts a new
        # reconstruction or acquisition job and does not change the selection.
        row=self.spectrum_canvas.display_row
        if row is not None:
            roi=(self.roi_from.value(),self.roi_to.value(),self.roi_terminal.isChecked()) if self.roi_enabled.isChecked() else None
            self.spectrum_canvas.show_interval(row,roi)
        else:
            apply_scale(self.spectrum_canvas.ax,'x',energy)
            apply_scale(self.spectrum_canvas.ax,'y',counts)
            self.spectrum_canvas.draw_idle()
        if self.analysis_rows:
            snapshot=self.last_status.get('last_snapshot') or self.analysis_rows[-1][3]
            self.charts.show_rows(self.analysis_rows,snapshot,self.last_status.get('mode','device'))
        else:
            apply_scale(self.charts.spg,'x',energy)
            apply_scale(self.charts.count,'y',counts)
            self.charts.draw_idle()
        for toolbar in (self.spectrum_toolbar,self.chart_toolbar):
            toolbar.update();toolbar.push_current()

    def select_interval(self,event):
        if event.inaxes!=self.charts.spg or event.ydata is None or not self.analysis_rows or self.chart_toolbar.mode:return
        ends=np.array([mdates.date2num(dt.datetime.fromtimestamp(row[0],dt.timezone.utc)) for row in self.analysis_rows])
        index=min(len(ends)-1,int(np.searchsorted(ends,event.ydata)))
        self.selected_analysis_row=self.analysis_rows[index]
        self.spectrum_mode.setCurrentIndex(1)
        self.refresh_spectrum(True)
        self.tabs.setCurrentIndex(1)

    def navigation_finished(self,event):
        if self.chart_toolbar.mode:self.follow_live.setChecked(False)

    def view_preferences(self):
        return dict(window=self.view_mode.currentData(),colormap=self.spectrogram_colormap.currentData())

    def restore_view(self,values):
        values=values if isinstance(values,dict) else {}
        for widget,value in ((self.view_mode,values.get('window')),(self.spectrogram_colormap,values.get('colormap'))):
            index=widget.findData(value) if value is not None else -1
            if index>=0:
                blocked=widget.blockSignals(True);widget.setCurrentIndex(index);widget.blockSignals(blocked)
        self.display_scales_changed()
