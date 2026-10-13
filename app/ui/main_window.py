"""Main window shell: header, layout, timers, preferences and the entry point."""
import bootstrap  # noqa: F401 -- adds app-local dependencies/ to sys.path
import argparse
import datetime as dt
import os
from pathlib import Path
import sys

from PySide6 import QtCore, QtGui, QtWidgets as Q
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT
from analysis import VERSION
import gui_preferences
import display_controls
from ui.analysis_panel import AnalysisUI
from ui.charts import Charts
from ui.paths import DATA_ROOT, ROOT, SETTINGS_DIR
from ui.run_status import RunStatusMixin
from ui.setup_panel import SetupPanelMixin
from ui.splash import SplashScreen, app_icon, set_app_user_model_id
from ui.theme import BG, PANEL, TEXT, TEAL, GOLD, MUTED, STYLESHEET


class Window(SetupPanelMixin, RunStatusMixin, AnalysisUI, Q.QMainWindow):
    def __init__(self, inspect_run=None, read_only=False):
        super().__init__()
        self.read_only=read_only
        # Shared settings first; then this release's own file and RadiaLog v0.5 (older layouts).
        preferences=(gui_preferences.read(SETTINGS_DIR) or gui_preferences.read(ROOT)
                     or gui_preferences.read(ROOT.parent/'radiacode_logger_v05'))
        remembered=preferences.get('run')
        if inspect_run is None and remembered and (Path(remembered)/'manifest.json').exists():inspect_run=remembered
        self.setWindowTitle(f'GammaKeeper {VERSION} — experiment recorder')
        self.resize(1500, 980)
        self.run = Path(inspect_run) if inspect_run else None
        self.recorder = None
        self.export_process = None
        self.export_log = None
        self.export_destination = None
        self.last_launch_log = None
        self.last_seq = None
        self.last_status = {}
        self.status_stale = False
        self.telemetry_reader=None
        self.telemetry_run=None
        self.settings_run = None
        self.run_settings = None
        self.setStyleSheet(STYLESHEET)
        main = Q.QWidget()
        self.setCentralWidget(main)
        layout = Q.QVBoxLayout(main)
        layout.setContentsMargins(24, 18, 24, 18)
        header = Q.QHBoxLayout()
        title = Q.QLabel('GammaKeeper')
        title.setObjectName('title')
        header.addWidget(title)
        subtitle = Q.QLabel('Spectra, counts and dose • durable experiment logging')
        subtitle.setObjectName('muted')
        header.addWidget(subtitle, 1)
        self.connection = Q.QLabel('Ready')
        self.connection.setStyleSheet(f'color: {TEAL}; font-weight: 600;')
        header.addWidget(self.connection)
        self.settings_toggle=Q.QToolButton();self.settings_toggle.setCheckable(True)
        self.settings_toggle.setToolTip('Show or hide run setup and recording controls')
        self.settings_toggle.toggled.connect(self._set_setup_visible)
        header.addWidget(self.settings_toggle)
        self.back_live_button=Q.QToolButton();self.back_live_button.setText('Live plots')
        self.back_live_button.setEnabled(False);self.back_live_button.clicked.connect(lambda:self.tabs.setCurrentIndex(0))
        header.addWidget(self.back_live_button)
        self.more_button=Q.QToolButton();self.more_button.setText('More ▾')
        self.more_menu=Q.QMenu(self.more_button);self.more_button.setMenu(self.more_menu)
        self.more_button.setPopupMode(Q.QToolButton.ToolButtonPopupMode.InstantPopup)
        header.addWidget(self.more_button)
        self.fullscreen_button=Q.QToolButton();self.fullscreen_button.setText('Full screen')
        self.fullscreen_button.clicked.connect(self.toggle_fullscreen);header.addWidget(self.fullscreen_button)
        self.live_stop_button=Q.QPushButton('Stop recording…');self.live_stop_button.setObjectName('stop')
        self.live_stop_button.clicked.connect(self.stop_recording);self.live_stop_button.setVisible(False)
        header.addWidget(self.live_stop_button)
        layout.addLayout(header)
        content = Q.QHBoxLayout()
        layout.addLayout(content, 1)
        settings = self._build_setup_panel(content)
        self.setup_scroll=content.itemAt(0).widget()
        right = Q.QVBoxLayout()
        content.addLayout(right, 1)
        self.run_context=Q.QLabel('Configure a recording or open a saved run.')
        self.run_context.setObjectName('muted');self.run_context.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.run_context.setToolTip('Run label, recorded interval, planned duration and end time')
        right.addWidget(self.run_context)
        metric_row=Q.QHBoxLayout();metric_row.setSpacing(24)
        self.metrics = []
        self.metric_captions=[]
        for text in ['Spectrum rate', 'Dose rate', 'Observed dose', 'Saved spectra']:
            frame=Q.QWidget();stack=Q.QVBoxLayout(frame);stack.setContentsMargins(0,0,0,0);stack.setSpacing(0)
            caption = Q.QLabel(text)
            caption.setObjectName('muted')
            self.metric_captions.append(caption)
            value = Q.QLabel('—')
            value.setStyleSheet(f'font-size: 13pt; font-weight: 600; color: {TEAL};')
            stack.addWidget(caption)
            stack.addWidget(value)
            self.metrics.append(value)
            if len(self.metrics)==3:
                self.observed_metric=frame
            elif len(self.metrics) in (1,2,4):
                metric_row.addWidget(frame)
        metric_row.addStretch();right.addLayout(metric_row)
        self.live_freshness=Q.QLabel('Waiting for current data')
        self.live_freshness.setObjectName('muted');right.addWidget(self.live_freshness)
        self.run_summary = Q.QLabel('Configure a new recording or open a saved run.')
        self.run_summary.setWordWrap(True)
        self.run_summary.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.run_summary.setObjectName('muted')
        self.charts = Charts()
        self.chart_toolbar=NavigationToolbar2QT(self.charts,self)
        self.init_analysis(right,ROOT,BG,PANEL,TEXT)
        self.audit_button=Q.QPushButton('Validate completed run + export')
        self.audit_button.clicked.connect(self.audit_selected)
        settings.insertWidget(settings.count()-2,self.audit_button)
        backup=Q.QPushButton('Create verified backup…');backup.clicked.connect(lambda:self.backup_selected(False))
        settings.insertWidget(settings.count()-2,backup)
        resume_backup=Q.QPushButton('Resume interrupted backup…');resume_backup.clicked.connect(lambda:self.backup_selected(True))
        settings.insertWidget(settings.count()-2,resume_backup)
        self.details_button=Q.QToolButton();self.details_button.setText('Run details & diagnostics  ▸')
        self.details_button.setCheckable(True);self.details_button.setChecked(False)
        self.details_container=Q.QWidget();details_layout=Q.QVBoxLayout(self.details_container)
        details_layout.setContentsMargins(0,0,0,0);details_layout.setSpacing(4)
        self.health=Q.QLabel('Waiting for run health')
        self.health.setObjectName('muted')
        self.health.setWordWrap(True)
        details_layout.addWidget(self.health)
        self.detail = Q.QLabel('Start a new run or open a saved run. Device accumulators and calibration are preserved.')
        self.detail.setObjectName('muted')
        self.detail.setWordWrap(True)
        details_layout.addWidget(self.detail)
        details_layout.addWidget(self.run_summary)
        self.observed_metric.setParent(self.details_container)
        details_layout.addWidget(self.observed_metric)
        self.log = Q.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(85)
        self.log.document().setMaximumBlockCount(300)
        details_layout.addWidget(self.log)
        self.footer = Q.QLabel('UTC uses the Windows clock. Raw records retain device flags and error percentages. Gaps are never filled.')
        self.footer.setObjectName('muted')
        self.footer.setWordWrap(True)
        details_layout.addWidget(self.footer)
        self.details_container.setVisible(False)
        self.details_button.toggled.connect(lambda shown:(self.details_container.setVisible(shown),
                                                          self.details_button.setText('Run details & diagnostics  ▾' if shown else 'Run details & diagnostics  ▸')))
        right.addWidget(self.details_button);right.addWidget(self.details_container)
        self.tabs.tabBar().hide()
        for index in range(1,self.tabs.count()):
            action=self.more_menu.addAction(self.tabs.tabText(index))
            action.triggered.connect(lambda checked=False,page=index:self.tabs.setCurrentIndex(page))
        self.tabs.currentChanged.connect(lambda index:self.back_live_button.setEnabled(index!=0))
        self._layout_run=object()
        self.fullscreen_shortcut=QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_F11),self)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        self.escape_shortcut=QtGui.QShortcut(QtGui.QKeySequence(QtCore.Qt.Key.Key_Escape),self)
        self.escape_shortcut.activated.connect(self.exit_fullscreen)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        if not self.run:
            values=preferences.get('setup') or {}
            self.label.setText(values.get('label',''))
            self.serial.setText(values.get('serial',''))
            self.interval.setValue(values.get('interval_s',10))
            self.days.setValue(values.get('days',7))
            self.folder.setText(values.get('folder',str(DATA_ROOT)))
            self.demo.setChecked(values.get('demo',False))
            monitor=values.get('monitoring') or {}
            self.monitor_enabled.setChecked(monitor.get('enabled',True));self.backup_parent.setText(monitor.get('backup_parent',''))
            if not self.serial.text().strip() and not self.demo.isChecked() and self._device_checks_enabled():
                self.detect_device()  # no remembered detector: use the one on USB, if exactly one
        display_controls.restore(self,preferences.get("display",{}))
        self.restore_view(preferences.get('view',{}))
        self._sync_recording_layout()
        self.refresh()

    def _set_setup_visible(self, shown):
        self.setup_scroll.setVisible(shown)
        self.settings_toggle.setText('Hide setup' if shown else 'Run setup')

    def _sync_recording_layout(self):
        selected=str(self.run.resolve()) if self.run else None
        if selected==self._layout_run:return
        self._layout_run=selected
        shown=selected is None
        blocked=self.settings_toggle.blockSignals(True)
        self.settings_toggle.setChecked(shown)
        self.settings_toggle.blockSignals(blocked)
        self._set_setup_visible(shown)
        self.tabs.setCurrentIndex(0)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self.showFullScreen();self.fullscreen_button.setText('Exit full screen')

    def exit_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
            self.fullscreen_button.setText('Full screen')

    def message(self, text):
        self.log.appendPlainText(dt.datetime.now().strftime('%H:%M:%S') + '  ' + text)

    def fail(self, error):
        self.message(str(error))
        Q.QMessageBox.warning(self, 'GammaKeeper', str(error))

    def closeEvent(self, event):
        # Recorder has its own process and saved control/status files; window closure
        # does not issue Stop or delete files. A later window can Open saved run.
        self._closing=True
        self.preview_cancel.set()
        self.accumulation_cancel.set()
        self.dashboard_accumulation_cancel.set()
        self.science_cancel.set()
        if self.read_only:
            event.accept();return
        self.save_preferences()
        event.accept()

    def save_preferences(self):
        """Persist the setup, selected run and display choices in the shared settings folder."""
        try:
            setup=dict(label=self.label.text(),serial=self.serial.text(),
                       interval_s=self.interval.value(),days=self.days.value(),
                       folder=self.folder.text(),demo=self.demo.isChecked())
            setup['monitoring']=dict(enabled=self.monitor_enabled.isChecked(),backup_parent=self.backup_parent.text())
        except ValueError:
            # Invalid unfinished text must neither prevent closing nor overwrite
            # the last valid schedule with a guessed value.
            setup=(gui_preferences.read(SETTINGS_DIR) or {}).get('setup',{})
        gui_preferences.save(SETTINGS_DIR,dict(run=str(self.run.resolve()) if self.run else None,setup=setup,display=display_controls.preferences(self),
                                       view=self.view_preferences()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspect-run', type=Path)
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--no-splash', action='store_true', help='start without the splash page')
    args = parser.parse_args()
    set_app_user_model_id()  # before any window exists, so the taskbar groups under GammaKeeper
    app = Q.QApplication(sys.argv[:1])
    app.setApplicationName('GammaKeeper')
    app.setWindowIcon(app_icon())
    # Explicit font bytes also make offscreen rendering reproducible in restricted
    # Windows sessions whose Qt platform plugin cannot enumerate system fonts.
    # Loaded before the splash so its text never falls back to missing glyphs.
    for name in ['segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf']:
        font = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / name
        if font.exists():
            QtGui.QFontDatabase.addApplicationFontFromData(QtCore.QByteArray(font.read_bytes()))
    splash = None
    if not args.no_splash:
        reopening = args.inspect_run or (gui_preferences.read(SETTINGS_DIR) or gui_preferences.read(ROOT) or {}).get('run')
        splash = SplashScreen(VERSION, ['Applying style', 'Building window and reading saved run',
                                        'Opening main window'], reopening)
        splash.show()
        splash.begin(0)
    app.setFont(QtGui.QFont('Segoe UI', 10))
    app.setStyle('Fusion')
    if splash:
        splash.begin(1)
    window = Window(args.inspect_run)
    if splash:
        splash.begin(2)
    window.show()
    if splash:
        splash.finish()
    if args.screenshot:
        capture_deadline=__import__('time').monotonic()+20
        def grab():
            window.refresh()
            if (window.preview_busy or (window.telemetry_reader and window.telemetry_reader.last_id<window.telemetry_reader.latest_batch_id)) and __import__('time').monotonic()<capture_deadline:
                QtCore.QTimer.singleShot(300,grab)
                return
            window.charts.draw()
            if not window.grab().save(str(args.screenshot)):
                raise RuntimeError('Screenshot save failed')
            app.quit()
        QtCore.QTimer.singleShot(1200, grab)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
