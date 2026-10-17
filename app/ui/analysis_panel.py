"""AnalysisUI: the tabbed analysis area, composed from the tab mixins."""
import datetime as dt
import threading
import time

import numpy as np
from PySide6 import QtCore, QtWidgets as Q
from matplotlib.widgets import SpanSelector
from system_time import boot_identity
import display_controls
from ui.events_tab import EventsMixin
from ui.jobs import JobsMixin
from ui.notes_tab import NotesMixin
from ui.spectrum_tab import SpectrumTabMixin
from ui.summary_tab import SummaryMixin
from ui.timeseries_tab import TimeSeriesMixin
from ui.experiment_tab import ExperimentMixin


class AnalysisUI(TimeSeriesMixin, SpectrumTabMixin, SummaryMixin, EventsMixin, NotesMixin, JobsMixin, ExperimentMixin):
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
        self.preview_key=None;self.analysis_rows=[];self.preview_summary={};self.preview_aggregation=None;self._closing=False
        self.preview_request_key=None;self.plot_preview_cache_status=''
        self.background_jobs=[];self._last_events=0;self._last_disk=0
        self.selected_analysis_row=None
        self.accumulation_pool=QtCore.QThreadPool(self);self.accumulation_pool.setMaxThreadCount(1)
        self.accumulation_cancel=threading.Event();self.accumulation_busy=False
        self.accumulation_key=None;self.accumulation_engine=None;self.accumulation_row=None;self.accumulation_head=-1;self.accumulation_error=None
        self.dashboard_accumulation_pool=QtCore.QThreadPool(self);self.dashboard_accumulation_pool.setMaxThreadCount(1)
        self.dashboard_accumulation_cancel=threading.Event();self.dashboard_accumulation_busy=False
        self.dashboard_accumulation_engine=None;self.dashboard_accumulation_key=None
        self.dashboard_accumulation_row=None;self.dashboard_accumulation_head=-1;self.dashboard_accumulation_error=None
        self.dashboard_accumulation_progress_text=''
        self.tabs=Q.QTabWidget()
        self._build_timeseries_tab()
        self._build_spectrum_tab(bg,panel,text)
        display_controls.connect_controls(self)
        self._spectrum_key=None
        self._build_summary_tab()
        self._build_events_tab()
        from time_export_ui import install_time_export
        install_time_export(self)
        self._build_notes_tab()
        self._build_experiment_tab()
        # Hidden advanced pages must not impose their wide control rows on the
        # dashboard's minimum width. Scroll them when the window is smaller.
        for index in range(1,self.tabs.count()):
            page=self.tabs.widget(index);label=self.tabs.tabText(index)
            if isinstance(page,Q.QScrollArea):continue
            self.tabs.removeTab(index)
            scroll=Q.QScrollArea();scroll.setWidgetResizable(True)
            scroll.setFrameShape(Q.QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.tabs.insertTab(index,scroll,label)
            if page is self.experiment_page:self.experiment_page=scroll
        right.addWidget(self.tabs,1)
        self.charts.mpl_connect('button_press_event',self.select_interval)
        self.charts.mpl_connect('button_release_event',self.navigation_finished)
        self.charts.mpl_connect('pick_event',self.toggle_axis_from_pick)
        self.spectrum_canvas.mpl_connect('pick_event',self.toggle_axis_from_pick)
        self.chart_energy_span=SpanSelector(self.charts.accum,self.select_energy_span,'horizontal',
                                            useblit=False,button=1,minspan=.01,
                                            props=dict(facecolor='#f5bc66',alpha=.25))
        self.spectrum_energy_span=SpanSelector(self.spectrum_canvas.ax,self.select_energy_span,'horizontal',
                                               useblit=False,button=1,minspan=.01,
                                               props=dict(facecolor='#f5bc66',alpha=.25))
        self.view_mode.currentIndexChanged.connect(lambda:self.request_preview(True))
        self.view_mode.currentIndexChanged.connect(lambda:self.request_dashboard_accumulation())
        self.view_epoch.currentIndexChanged.connect(lambda:self.request_preview(True))
        self.view_epoch.currentIndexChanged.connect(lambda:self.request_dashboard_accumulation())
        self.time_range_slider.rangeChanged.connect(self._time_slider_changed)
        self.time_range_slider.rangeCommitted.connect(self.apply_time_range)
        for edit in (self.view_from,self.view_until):edit.editingFinished.connect(self.apply_time_range)
        self.energy_range_slider.rangeChanged.connect(self._energy_slider_changed)
        self.energy_range_slider.rangeCommitted.connect(self.apply_energy_range)
        self.spectrum_energy_slider.rangeChanged.connect(self._energy_slider_changed)
        self.spectrum_energy_slider.rangeCommitted.connect(self.apply_energy_range)
        for control in (self.axis_timezone,self.color_percentile,self.roi_from,self.roi_to):
            signal=control.currentIndexChanged if isinstance(control,Q.QComboBox) else control.valueChanged
            signal.connect(self.redraw_analysis)
        self.roi_enabled.toggled.connect(self.redraw_analysis);self.roi_terminal.toggled.connect(self.redraw_analysis)
        self.follow_live.toggled.connect(lambda checked:self.request_preview(True) if checked else None)
        self.tabs.currentChanged.connect(self.spectrum_tab_changed)
        self.spectrum_mode.currentIndexChanged.connect(lambda:self.refresh_spectrum(True))
        self.spectrum_refresh.clicked.connect(lambda:self.refresh_spectrum(True))
        self.collection_epoch.currentIndexChanged.connect(lambda:self.refresh_spectrum(True))
        self.collection_latest.toggled.connect(lambda:self.refresh_spectrum(True))
        # Both tabs edit the same ROI; the controls remain usable in either view.
        for original,mirror in ((self.roi_from,self.spectrum_roi_from),(self.roi_to,self.spectrum_roi_to)):
            original.valueChanged.connect(mirror.setValue);mirror.valueChanged.connect(original.setValue)
            original.valueChanged.connect(self._energy_boxes_changed)
        for original,mirror in ((self.roi_enabled,self.spectrum_roi_enabled),(self.roi_terminal,self.spectrum_roi_terminal)):
            original.toggled.connect(mirror.setChecked);mirror.toggled.connect(original.setChecked)
        self.view_from.setToolTip('Select saved intervals whose end timestamps lie in this UTC window; counts are not redistributed.')
        self.view_until.setToolTip(self.view_from.toolTip())

    def toggle_axis_from_pick(self,event):
        artist=event.artist
        axes=((self.charts.spg.xaxis.label,self.energy_axis),
              (self.charts.accum.xaxis.label,self.energy_axis),
              (self.charts.accum.yaxis.label,self.spectrum_counts_axis),
              (self.charts.count.yaxis.label,self.counts_axis),
              (self.charts.dose.yaxis.label,self.dose_axis),
              (self.spectrum_canvas.ax.xaxis.label,self.energy_axis),
              (self.spectrum_canvas.ax.yaxis.label,self.spectrum_counts_axis))
        for label,control in axes:
            if artist is label:
                control.setCurrentIndex(1-control.currentIndex())
                return

    def select_energy_span(self,low,high):
        toolbar=self.spectrum_toolbar if self.tabs.currentIndex()==1 else self.chart_toolbar
        if toolbar.mode or high<=low:return
        self.energy_range_slider.set_values(low,high,emit=False)
        self._energy_slider_changed(*self.energy_range_slider.values())
        self.roi_enabled.setChecked(True)
        self.redraw_analysis()

    def fit_spectrum_region(self,zoom_x=False):
        on_tab=self.tabs.currentIndex()==1
        row=self.spectrum_canvas.display_row if on_tab else self.charts.accumulated_row
        ax=self.spectrum_canvas.ax if on_tab else self.charts.accum
        if row is None or row[2].get('counts_delta') is None:return
        counts=np.asarray(row[2]['counts_delta'][:-1],dtype=float)
        a0,a1,a2=row[3]['spectrum']['calibration_keV']
        channels=np.arange(len(counts))
        energy=a0+a1*channels+a2*channels**2
        low,high=self.energy_range_slider.values() if self.roi_enabled.isChecked() else ax.get_xlim()
        selected=counts[(energy>=low)&(energy<=high)]
        selected=selected[np.isfinite(selected)]
        if ax.get_yscale()=='log':selected=selected[selected>0]
        if not selected.size:return
        bottom,top=float(selected.min()),float(selected.max())
        if ax.get_yscale()=='log':
            bottom=max(np.finfo(float).tiny,bottom/1.2)
            top=max(bottom*1.1,top*1.2)
        else:
            margin=max((top-bottom)*.07,1.)
            bottom=max(0.,bottom-margin);top+=margin
        xlim=(low,high) if zoom_x else ax.get_xlim()
        if zoom_x and ax.get_xscale()=='log':
            positive=energy[energy>0]
            if not len(positive):return
            xlim=(max(low,float(positive.min())),high)
        if xlim[1]<=xlim[0]:return
        ax.set_xlim(*xlim);ax.set_ylim(bottom,top)
        if on_tab:self.spectrum_canvas.view_limits=(xlim,(bottom,top))
        else:self.charts.accum_view=(xlim,(bottom,top))
        ax.figure.canvas.draw_idle()

    def reset_spectrum_axes(self):
        if self.tabs.currentIndex()==1:
            self.spectrum_canvas.view_limits=None
            row=self.spectrum_canvas.display_row
            if row is not None:
                roi=(self.roi_from.value(),self.roi_to.value(),self.roi_terminal.isChecked()) if self.roi_enabled.isChecked() else None
                self.spectrum_canvas.show_interval(row,roi)
        else:
            self.charts.accum_view=None
            self.charts._draw_accumulated()
            self.charts.draw_idle()
