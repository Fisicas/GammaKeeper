"""Time series tab: background previews, heatmap selection and display scales."""
import datetime as dt
import math
import sqlite3
import threading
import time

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as Q
import matplotlib.dates as mdates
from export_run import readonly
from plot_scales import apply_scale
import display_controls
import gui_preferences
from ui.paths import ANALYSIS_CACHE_DIR
from ui.range_slider import RangeSlider
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
        self.plot_options_button=Q.QToolButton();self.plot_options_button.setText('Plot options [show]')
        self.plot_options_button.setCheckable(True);self.plot_options_button.setChecked(False)
        self.plot_options=Q.QFrame();self.plot_options.setObjectName('panel')
        options_layout=Q.QVBoxLayout(self.plot_options);options_layout.setContentsMargins(8,8,8,8)
        self.plot_refresh=Q.QPushButton('Refresh plots');self.plot_refresh.clicked.connect(self.refresh_live_plots)
        self.plot_render_status=Q.QLabel('')
        self.plot_render_status.setAccessibleName('Plot rendering status')
        self.plot_render_status.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight|QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.plot_render_status.setFixedSize(520,24)
        self.plot_render_status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.plot_render_status.setStyleSheet(f'color: {GOLD}; font-weight: 600;')
        self.plot_render_status.setVisible(False)
        actions.addWidget(self.plot_options_button);actions.addStretch()
        actions.addWidget(self.plot_render_status);actions.addWidget(self.plot_refresh)
        layout.addLayout(actions)
        layout.addWidget(self.plot_options)
        self.plot_options.setVisible(False)
        self.plot_options_button.toggled.connect(lambda shown:(self.plot_options.setVisible(shown),
                                                               self.plot_options_button.setText('Plot options [hide]' if shown else 'Plot options [show]')))
        controls=Q.QHBoxLayout()
        self.view_mode=Q.QComboBox()
        for text,data in VIEW_WINDOWS:self.view_mode.addItem(text,data)
        self.view_mode.setCurrentIndex(self.view_mode.findData(DEFAULT_WINDOW))
        self.view_mode.setToolTip('Time span of the spectrogram, count-rate and dose-rate plots. Windows are measured back from the latest saved spectrum; long spans group intervals (counts summed, divided by device seconds).')
        self.view_epoch=Q.QComboBox();self.view_epoch.addItem('All epochs',None)
        self.follow_live=Q.QCheckBox('Follow live');self.follow_live.setChecked(True)
        self.axis_timezone=Q.QComboBox();self.axis_timezone.addItems(['UTC','Local time'])
        self.color_percentile=Q.QDoubleSpinBox();self.color_percentile.setRange(90,100);self.color_percentile.setDecimals(1);self.color_percentile.setValue(100);self.color_percentile.setSuffix(' % color limit')
        self.color_percentile.setToolTip('100% shows the full displayed intensity range. Lower values clip high cells and can change the apparent width of fading bands.')
        for widget in (self.view_mode,self.view_epoch,self.follow_live,self.axis_timezone,self.color_percentile):controls.addWidget(widget)
        options_layout.addLayout(controls)
        options_layout.addLayout(display_controls.controls(self))
        self.display_scale_note=Q.QLabel('Scales are display-only; logarithmic axes hide nonpositive values. Spectrum-count and spectrogram-intensity scales are independent of the time-series count-rate scale.')
        self.display_scale_note.setWordWrap(True)
        self.display_scale_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        options_layout.addWidget(self.display_scale_note)
        time_controls=Q.QHBoxLayout()
        self.view_from=Q.QDateTimeEdit();self.view_until=Q.QDateTimeEdit()
        for edit in (self.view_from,self.view_until):
            edit.setDisplayFormat('yyyy-MM-dd HH:mm:ss');edit.setCalendarPopup(True);edit.setTimeZone(QtCore.QTimeZone(0))
            edit.setEnabled(False)
        self.time_range_slider=RangeSlider();self.time_range_slider.setEnabled(False)
        time_controls.addWidget(Q.QLabel('Time UTC'));time_controls.addWidget(self.view_from)
        time_controls.addWidget(self.time_range_slider,1);time_controls.addWidget(self.view_until)
        layout.addLayout(time_controls)
        self.roi_enabled=Q.QCheckBox('ROI')
        self.roi_from=Q.QDoubleSpinBox();self.roi_to=Q.QDoubleSpinBox()
        for spin in (self.roi_from,self.roi_to):spin.setRange(0,10000);spin.setDecimals(1);spin.setSuffix(' keV')
        self.roi_from.setValue(500);self.roi_to.setValue(700)
        self.roi_terminal=Q.QCheckBox('Include terminal channel')
        energy_controls=Q.QHBoxLayout()
        self.energy_range_slider=RangeSlider();self.energy_range_slider.set_domain(0,10000)
        self.energy_range_slider.set_values(500,700,emit=False)
        for widget in (self.roi_enabled,self.roi_from,self.energy_range_slider,self.roi_to):
            energy_controls.addWidget(widget,1 if widget is self.energy_range_slider else 0)
        energy_controls.addWidget(self.roi_terminal)
        layout.addLayout(energy_controls)
        axis_controls=Q.QHBoxLayout();self.axis_buttons={}
        for key,label,control in (('energy','Energy X',self.energy_axis),
                                  ('spectrum','Spectrum Y',self.spectrum_counts_axis),
                                  ('rate','Rate Y',self.counts_axis),
                                  ('dose','Dose Y',self.dose_axis),
                                  ('intensity','Heatmap color',self.intensity_scale)):
            button=Q.QPushButton();button.setToolTip('Toggle this display axis between linear and logarithmic')
            button.clicked.connect(lambda checked=False,selector=control:selector.setCurrentIndex(1-selector.currentIndex()))
            control.currentIndexChanged.connect(lambda index,b=button,t=label,c=control:b.setText(f'{t}: {c.currentText()}'))
            button.setText(f'{label}: {control.currentText()}')
            self.axis_buttons[key]=button;axis_controls.addWidget(button)
        self.fit_ordinate_button=Q.QPushButton('Fit Y to region')
        self.fit_ordinate_button.clicked.connect(lambda:self.fit_spectrum_region(False))
        self.zoom_region_button=Q.QPushButton('Zoom to region')
        self.zoom_region_button.clicked.connect(lambda:self.fit_spectrum_region(True))
        self.reset_axes_button=Q.QPushButton('Reset X/Y')
        self.reset_axes_button.clicked.connect(self.reset_spectrum_axes)
        for button in (self.fit_ordinate_button,self.zoom_region_button,self.reset_axes_button):axis_controls.addWidget(button)
        layout.addLayout(axis_controls)
        self.preview_note=Q.QLabel('Plots are derived views; original records remain saved. Drag on the accumulated spectrum to select energy; use the time range for a complete-interval accumulation.')
        self.preview_note.setWordWrap(True);self.preview_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        options_layout.addWidget(self.preview_note)
        self.plot_cadence=Q.QLabel('Acquisition interval: waiting for run settings · displayed grouping: waiting for plot data')
        self.plot_cadence.setObjectName('muted')
        layout.addWidget(self.plot_cadence)
        self.plot_preview_error=''
        self.plot_accumulation_error=''
        self.plot_control_error=''
        legend=Q.QHBoxLayout();legend.setSpacing(18)
        self.series_buttons={}
        for key,label,color,dashed in (
                ('counts','Spectrum differences · cps',TEAL,False),
                ('estimator','Device estimator · cps',MUTED,True),
                ('dose','Device dose rate · µSv/h',GOLD,False)):
            swatch=QtGui.QPixmap(24,12);swatch.fill(QtCore.Qt.GlobalColor.transparent)
            painter=QtGui.QPainter(swatch);pen=QtGui.QPen(QtGui.QColor(color));pen.setWidth(2)
            if dashed:pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen);painter.drawLine(1,6,23,6);painter.end()
            button=Q.QToolButton();button.setText(label);button.setIcon(QtGui.QIcon(swatch))
            button.setIconSize(QtCore.QSize(24,12));button.setCheckable(True);button.setChecked(True)
            button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setStyleSheet(f'QToolButton {{ color: {color}; background: transparent; font-weight: 500; padding: 2px; }} QToolButton:!checked {{ color: {MUTED}; }}')
            button.toggled.connect(lambda checked,name=key:self.charts.set_series_visible(name,checked))
            self.series_buttons[key]=button;legend.addWidget(button)
        legend.addStretch();layout.addLayout(legend)
        layout.addWidget(self.chart_toolbar);layout.addWidget(self.charts,1)
        self.tabs.addTab(page,'Live dashboard')

    def _sync_time_boxes(self,low,high):
        for edit,value in ((self.view_from,low),(self.view_until,high)):
            blocked=edit.blockSignals(True)
            edit.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(round(value),QtCore.QTimeZone(0)))
            edit.blockSignals(blocked)

    def _update_time_domain(self,first,last):
        if first is None or last is None or last<=first:
            return
        # The editable boxes display whole UTC seconds. Enclose fractional
        # snapshot timestamps so releasing an endpoint handle stays valid.
        first,last=math.floor(first),math.ceil(last)
        run_key=str(self.run.resolve())
        old_run=getattr(self,'_time_domain_run',None)
        old_domain=self.time_range_slider.domain()
        selected=self.view_mode.currentData()=='window' and old_run==run_key
        if old_run!=run_key or old_domain!=(float(first),float(last)):
            self.time_range_slider.set_domain(first,last,preserve=selected)
            if not selected:self._sync_time_boxes(first,last)
            self._time_domain_run=run_key
        for widget in (self.time_range_slider,self.view_from,self.view_until):widget.setEnabled(True)

    def _time_slider_changed(self,low,high):
        self._sync_time_boxes(low,high)

    def apply_time_range(self,*unused):
        if not self.time_range_slider.isEnabled():return
        low=self.view_from.dateTime().toSecsSinceEpoch()
        high=self.view_until.dateTime().toSecsSinceEpoch()
        domain=self.time_range_slider.domain()
        if high<=low or low<domain[0] or high>domain[1]:
            self.plot_control_error='Choose increasing UTC bounds inside the saved run.'
            self.update_plot_render_status()
            return
        self.plot_control_error=''
        self.update_plot_render_status()
        self.time_range_slider.set_values(low,high,emit=False)
        index=self.view_mode.findData('window')
        if self.view_mode.currentIndex()!=index:
            self.view_mode.setCurrentIndex(index)
        else:
            self.request_preview(True)
            self.request_dashboard_accumulation()

    def _energy_slider_changed(self,low,high):
        for slider in (self.energy_range_slider,self.spectrum_energy_slider):
            slider.set_values(low,high,emit=False)
        for spin,value in ((self.roi_from,low),(self.roi_to,high),
                           (self.spectrum_roi_from,low),(self.spectrum_roi_to,high)):
            blocked=spin.blockSignals(True);spin.setValue(value);spin.blockSignals(blocked)

    def apply_energy_range(self,*unused):
        if self.roi_to.value()<=self.roi_from.value():
            self.plot_control_error='ROI upper energy must exceed lower energy.'
            self.update_plot_render_status()
            return
        self.plot_control_error=''
        self.update_plot_render_status()
        self.roi_enabled.setChecked(True)
        self.redraw_analysis()

    def _energy_boxes_changed(self,*unused):
        for slider in (self.energy_range_slider,self.spectrum_energy_slider):
            slider.set_values(self.roi_from.value(),self.roi_to.value(),emit=False)
        if self.roi_to.value()>self.roi_from.value():self.roi_enabled.setChecked(True)

    def _update_energy_domain(self,snapshot):
        spectrum=snapshot.get('spectrum') or {}
        counts=spectrum.get('counts') or []
        if not counts:return
        a0,a1,a2=spectrum['calibration_keV']
        energy=np.asarray([a0,a0+a1*(len(counts)-1)+a2*(len(counts)-1)**2])
        low,high=float(energy.min()),float(energy.max())
        if high<=low:return
        if self.energy_range_slider.domain()!=(low,high):
            self.energy_range_slider.set_domain(low,high,preserve=True)
            self.spectrum_energy_slider.set_domain(low,high,preserve=True)
            for spin in (self.roi_from,self.roi_to,self.spectrum_roi_from,self.spectrum_roi_to):
                blocked=spin.blockSignals(True);spin.setRange(low,high);spin.blockSignals(blocked)
            self._energy_slider_changed(*self.energy_range_slider.values())

    def refresh_live_plots(self):
        """Refresh derived views from saved records and report background work."""
        if not self.run or not (self.run/'run.sqlite').exists() or self._closing:
            self.plot_render_status.setText('Open a saved run to refresh its plots.')
            self.plot_render_status.setVisible(True)
            return
        self.request_preview(True)
        self.request_dashboard_accumulation()
        self.update_plot_render_status()

    def update_plot_render_status(self):
        """Summarize active plot work without obscuring the plots themselves."""
        messages=[]
        if self.preview_busy or self.preview_pending:
            messages.append('Rendering spectrogram and count/dose plots…')
        if self.plot_preview_cache_status:
            messages.append(self.plot_preview_cache_status)
        if getattr(self,'dashboard_accumulation_busy',False):
            messages.append(getattr(self,'dashboard_accumulation_progress_text','')
                            or 'Building accumulated spectrum from verified saved intervals…')
        if self.plot_preview_error:
            messages.append(self.plot_preview_error)
        if self.plot_accumulation_error:
            messages.append(self.plot_accumulation_error)
        if self.plot_control_error:
            messages.append(self.plot_control_error)
        status='  ·  '.join(messages)
        self.plot_render_status.setToolTip(status)
        self.plot_render_status.setText(self.plot_render_status.fontMetrics().elidedText(
            status,QtCore.Qt.TextElideMode.ElideRight,self.plot_render_status.width()-8))
        self.plot_render_status.setVisible(bool(messages))

    def request_preview(self,force=False):
        if not self.run or not (self.run/'run.sqlite').exists() or self._closing:
            return
        if not force and not self.follow_live.isChecked():return
        if not force and self.view_mode.currentData()=='full' and time.monotonic()-getattr(self,'_last_preview_request',-float('inf'))<60:
            return
        if self.preview_busy:
            if force:
                self.plot_preview_error=''
                self.preview_cancel.set();self.preview_pending=True
                self.preview_request_key=None
                self.update_plot_cadence()
                self.update_plot_render_status()
            return
        try:
            mode=self.view_mode.currentData()
            view=mode if mode in ('recent','full') else None
            db=readonly(self.run/'run.sqlite')
            try:
                head=db.execute('SELECT coalesce(max(seq),-1) FROM snapshots').fetchone()[0]
                time_bounds=db.execute('SELECT min(utc),max(utc) FROM snapshots').fetchone()
                self._update_time_domain(*time_bounds)
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
            self.plot_preview_error=''
            self.plot_preview_cache_status=''
            self.update_plot_cadence()
            self.update_plot_render_status()
            self._last_preview_request=time.monotonic()
            self._preview_forced=force
            cache=None if gui_preferences.test_mode() else ANALYSIS_CACHE_DIR/'previews'
            task=PreviewTask(self.run,view,self.view_epoch.currentData(),key,self.preview_cancel,cache)
            self.preview_request_key=key
            self._preview_task=task
            task.signals.progress.connect(self.preview_note.setText)
            task.signals.snapshot.connect(self.preview_snapshot)
            task.signals.done.connect(self.preview_done)
            self.preview_pool.start(task)
        except (OSError,ValueError,sqlite3.Error) as error:
            self.preview_note.setText(str(error))
            self.plot_preview_error='Plot unavailable: '+str(error)
            self.update_plot_render_status()

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

    def preview_snapshot(self,message):
        if self._closing or message.get('key')!=self.preview_request_key:return
        result=message.get('result')
        if not result:return
        state=result.get('verification_state')
        if state=='cached':
            self.plot_preview_cache_status=(f"Showing saved plots through spectrum {result['head']:,}; "
                'checking the cache endpoint and newer spectra. Full archive audit is separate.')
        else:
            self.plot_preview_cache_status=f"Showing an initial verified preview through spectrum {result['head']:,}; loading the rest…"
        self._apply_preview_result(result,provisional=True)

    def _apply_preview_result(self,result,provisional=False):
        self.analysis_rows=result['rows'];self.preview_summary=result['summary']
        if self.analysis_rows:self._update_energy_domain(self.analysis_rows[-1][3])
        self.preview_aggregation=int(result['aggregation'])
        self.charts.display_label=f"{len(self.analysis_rows)} displayed intervals • up to {result['aggregation']} recorded intervals / group"
        self.update_plot_cadence()
        if result.get('view')=='full' and 'accumulated_row' in result:
            self.dashboard_accumulation_key=str(self.run.resolve())
            self.dashboard_accumulation_row=result.get('accumulated_row')
            self.dashboard_accumulation_head=result['head']
            self.dashboard_accumulation_error=result.get('accumulated_error')
            status=('cached checkpoint' if provisional and result.get('verification_state')=='cached'
                    else 'verified partial' if provisional else '')
            self.charts.set_accumulated(self.dashboard_accumulation_row,self.dashboard_accumulation_error,status)
        if provisional:
            state=result.get('verification_state')
            basis='Saved cached display' if state=='cached' else 'Initial verified display'
            self.preview_note.setText(f"{basis} through spectrum {result['head']:,}; current source verification is still running. "
                                      'Full integrity audit is separate.')
        else:
            kind='Cached prefix + verified new intervals' if result.get('cached') else 'Verified raw reconstruction'
            checked=result.get('source_verified_utc')
            self.preview_note.setText(f"{kind} through spectrum {result['head']:,}; source check {checked}. "
                                      'Groups sum counts / device seconds; gaps remain blank. Full integrity audit is separate. Click a heatmap interval to inspect its spectrum.')
            self.plot_preview_cache_status=''
        if self.follow_live.isChecked() or self._preview_forced:self.redraw_analysis()
        self.update_plot_render_status()

    def preview_done(self,message):
        self.preview_busy=False
        if self._closing:return
        if self.preview_pending:
            self.preview_pending=False;self.request_preview(True);return
        if (not self.run or message['key'][0]!=str(self.run.resolve())
                or message['key']!=self.preview_request_key):
            self.update_plot_render_status();return
        if message['error']:
            self.preview_note.setText('Plot unavailable: '+message['error'])
            self.plot_preview_error='Plot unavailable: '+message['error']
            if self.plot_preview_cache_status.startswith('Showing saved plots'):
                self.plot_preview_cache_status='Saved cached plots remain visible, but the current source check failed.'
            elif self.plot_preview_cache_status.startswith('Showing an initial verified preview'):
                self.plot_preview_cache_status='The initial verified preview remains visible, but loading the full source stopped with an error.'
            self.update_plot_render_status();return
        result=message['result']
        if result is None:
            self.update_plot_render_status();return
        self.preview_key=message['key']
        self.plot_preview_error=''
        self._apply_preview_result(result)

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
        spectrum_counts=self.spectrum_counts_axis.currentData()
        self.spectrum_canvas.energy_scale=energy;self.spectrum_canvas.counts_scale=spectrum_counts
        self.charts.energy_scale=energy;self.charts.counts_scale=counts
        self.charts.dose_scale=self.dose_axis.currentData()
        self.charts.spectrum_counts_scale=spectrum_counts
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
            apply_scale(self.spectrum_canvas.ax,'y',spectrum_counts)
            self.spectrum_canvas.draw_idle()
        if self.analysis_rows:
            snapshot=self.last_status.get('last_snapshot') or self.analysis_rows[-1][3]
            self.charts.show_rows(self.analysis_rows,snapshot,self.last_status.get('mode','device'))
        else:
            apply_scale(self.charts.spg,'x',energy)
            apply_scale(self.charts.count,'y',counts)
            apply_scale(self.charts.dose,'y',self.dose_axis.currentData())
            self.charts.set_accumulated(self.dashboard_accumulation_row,self.dashboard_accumulation_error)
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
