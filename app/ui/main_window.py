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
from ui.paths import DATA_ROOT, ROOT
from ui.run_status import RunStatusMixin
from ui.setup_panel import SetupPanelMixin
from ui.splash import SplashScreen, app_icon, set_app_user_model_id
from ui.theme import BG, PANEL, TEXT, TEAL, STYLESHEET


class Window(SetupPanelMixin, RunStatusMixin, AnalysisUI, Q.QMainWindow):
    def __init__(self, inspect_run=None):
        super().__init__()
        preferences=gui_preferences.read(ROOT)
        if not preferences:
            preferences=gui_preferences.read(ROOT.parent/'radiacode_logger_v05')
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
        layout.addLayout(header)
        content = Q.QHBoxLayout()
        layout.addLayout(content, 1)
        settings = self._build_setup_panel(content)
        right = Q.QVBoxLayout()
        content.addLayout(right, 1)
        cards = Q.QHBoxLayout()
        self.metrics = []
        self.metric_captions=[]
        for text in ['Count rate • spectrum', 'Dose rate • latest', 'Dose • observed', 'Saved spectra']:
            frame = Q.QFrame()
            frame.setObjectName('panel')
            stack = Q.QVBoxLayout(frame)
            caption = Q.QLabel(text)
            caption.setObjectName('muted')
            self.metric_captions.append(caption)
            value = Q.QLabel('—')
            value.setObjectName('metric')
            stack.addWidget(caption)
            stack.addWidget(value)
            self.metrics.append(value)
            cards.addWidget(frame)
        right.addLayout(cards)
        self.run_summary = Q.QLabel('Configure a new recording or open a saved run.')
        self.run_summary.setWordWrap(True)
        self.run_summary.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.run_summary.setObjectName('muted')
        right.addWidget(self.run_summary)
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
        self.health=Q.QLabel('Waiting for run health')
        self.health.setObjectName('muted')
        self.health.setWordWrap(True)
        right.addWidget(self.health)
        self.detail = Q.QLabel('Start a new run or open a saved run. Device accumulators and calibration are preserved.')
        self.detail.setObjectName('muted')
        self.detail.setWordWrap(True)
        right.addWidget(self.detail)
        self.log = Q.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(85)
        self.log.document().setMaximumBlockCount(300)
        right.addWidget(self.log)
        self.footer = Q.QLabel('UTC uses the Windows clock. Raw records retain device flags and error percentages. Gaps are never filled.')
        self.footer.setObjectName('muted')
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)
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
        display_controls.restore(self,preferences.get("display",{}))
        self.restore_view(preferences.get('view',{}))
        self.refresh()

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
        try:
            setup=dict(label=self.label.text(),serial=self.serial.text(),
                       interval_s=self.interval.value(),days=self.days.value(),
                       folder=self.folder.text(),demo=self.demo.isChecked())
        except ValueError:
            # Invalid unfinished text must neither prevent closing nor overwrite
            # the last valid schedule with a guessed value.
            setup=(gui_preferences.read(ROOT) or {}).get('setup',{})
        gui_preferences.save(ROOT,dict(run=str(self.run.resolve()) if self.run else None,setup=setup,display=display_controls.preferences(self),
                                       view=self.view_preferences()))
        event.accept()


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
        reopening = args.inspect_run or (gui_preferences.read(ROOT) or {}).get('run')
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
