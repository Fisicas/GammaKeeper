"""AnalysisUI: the tabbed analysis area, composed from the tab mixins."""
import datetime as dt
import threading
import time

from PySide6 import QtCore, QtWidgets as Q
from system_time import boot_identity
import display_controls
from ui.events_tab import EventsMixin
from ui.jobs import JobsMixin
from ui.spectrum_tab import SpectrumTabMixin
from ui.summary_tab import SummaryMixin
from ui.timeseries_tab import TimeSeriesMixin


class AnalysisUI(TimeSeriesMixin, SpectrumTabMixin, SummaryMixin, EventsMixin, JobsMixin):
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
        self.accumulation_pool=QtCore.QThreadPool(self);self.accumulation_pool.setMaxThreadCount(1)
        self.accumulation_cancel=threading.Event();self.accumulation_busy=False
        self.accumulation_key=None;self.accumulation_engine=None;self.accumulation_row=None;self.accumulation_head=-1;self.accumulation_error=None
        self.tabs=Q.QTabWidget()
        self._build_timeseries_tab()
        self._build_spectrum_tab(bg,panel,text)
        display_controls.connect_controls(self)
        self._spectrum_key=None
        self._build_summary_tab()
        self._build_events_tab()
        from time_export_ui import install_time_export
        install_time_export(self)
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
        self.collection_epoch.currentIndexChanged.connect(lambda:self.refresh_spectrum(True))
        self.collection_latest.toggled.connect(lambda:self.refresh_spectrum(True))
        # Both tabs edit the same ROI; the controls remain usable in either view.
        for original,mirror in ((self.roi_from,self.spectrum_roi_from),(self.roi_to,self.spectrum_roi_to)):
            original.valueChanged.connect(mirror.setValue);mirror.valueChanged.connect(original.setValue)
        for original,mirror in ((self.roi_enabled,self.spectrum_roi_enabled),(self.roi_terminal,self.spectrum_roi_terminal)):
            original.toggled.connect(mirror.setChecked);mirror.toggled.connect(original.setChecked)
        self.view_from.setToolTip('Select saved intervals whose end timestamps lie in this UTC window; counts are not redistributed.')
        self.view_until.setToolTip(self.view_from.toolTip())
