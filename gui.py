"""Native Windows GUI for the independent RadiaLog recorder."""
import bootstrap
import argparse
from collections import deque
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as Q
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import matplotlib.dates as mdates
from matplotlib.colors import LogNorm
import matplotlib

from analysis import VERSION, midpoint, timestamp, DOSE_R_TO_USV
from export_run import readonly

ROOT = Path(__file__).resolve().parent
BG, PANEL, TEXT, MUTED, TEAL, GOLD = '#101923', '#182532', '#e6edf3', '#93a6b8', '#43d8bf', '#f5bc66'
matplotlib.rcParams.update({'text.color': TEXT, 'axes.titlecolor': TEXT, 'axes.titlesize': 10})


class Charts(FigureCanvasQTAgg):
    def __init__(self):
        self.figure = Figure(figsize=(10, 7), facecolor=BG, layout='constrained')
        super().__init__(self.figure)
        grid = self.figure.add_gridspec(2, 2, height_ratios=[2.3, 1])
        self.spg = self.figure.add_subplot(grid[0, :])
        self.count = self.figure.add_subplot(grid[1, 0])
        self.dose = self.figure.add_subplot(grid[1, 1])
        self.colorbar = None
        self.empty()

    def style(self, ax):
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=MUTED, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#314357')
        ax.xaxis.label.set_color(MUTED)
        ax.yaxis.label.set_color(MUTED)
        ax.title.set_color(TEXT)
        ax.title.set_fontsize(10)

    def empty(self):
        for ax, title in [(self.spg, 'Energy spectrum over time'), (self.count, 'Count rate'), (self.dose, 'Dose rate')]:
            self.style(ax)
            ax.set_title(title, loc='left')
            ax.text(.5, .5, 'Waiting for recorded data', transform=ax.transAxes, ha='center', va='center', color=MUTED)
        self.draw_idle()

    def show_rows(self, rows, last_snapshot, mode):
        if self.colorbar:
            self.colorbar.remove()
            self.colorbar = None
        for ax in [self.spg, self.count, self.dose]:
            ax.clear()
            self.style(ax)
        spectrum = last_snapshot['spectrum']
        channels = len(spectrum['counts'])
        edge_ch = np.arange(channels + 1) - .5
        a0, a1, a2 = spectrum['calibration_keV']
        energy = a0 + a1 * edge_ch + a2 * edge_ch**2
        ends, starts, matrix, rates, dose_rates, filtered = [], [], [], [], [], []
        for utc, epoch, d, s in rows:
            if d['device_duration_s'] is None or d['wall_duration_s'] is None or d['wall_duration_s'] <= 0:
                continue
            end = mdates.date2num(dt.datetime.fromtimestamp(utc, dt.timezone.utc))
            start = end - d['wall_duration_s'] / 86400
            is_normal = d['quality'] == ['normal'] and not s.get('final_partial_interval')
            delta = d['counts_delta']
            if delta is not None and len(delta) == channels and s['spectrum']['calibration_keV'] == spectrum['calibration_keV']:
                values = np.asarray(delta, dtype=float) / d['device_duration_s'] if is_normal else np.full(channels, np.nan)
            else:
                values = np.full(channels, np.nan)
            starts.append(start)
            ends.append(end)
            matrix.append(values)
            rates.append(d['count_rate_cps'] if is_normal else np.nan)
            tb = s.get('telemetry_bin', {})
            coverage = tb.get('coverage_fraction', 0)
            dose_rates.append(tb.get('dose_rate_uSv_h') if coverage >= .999 and is_normal else np.nan)
            filtered.append(tb.get('device_count_rate_cps') if coverage >= .999 and is_normal else np.nan)
        prefix = 'SIMULATED • ' if mode == 'demo' else ''
        self.spg.set_title(prefix + 'Spectrogram • recent 240 intervals • gaps left blank', loc='left')
        if ends and np.all(np.diff(energy) > 0):
            yedges = [starts[0]] + ends
            values = np.ma.masked_invalid(np.asarray(matrix))
            positive = values.compressed()
            positive = positive[positive > 0]
            maximum = max(float(np.percentile(positive, 99.5)), .1) if len(positive) else 1.
            cmap = __import__('matplotlib').colormaps['magma'].copy()
            cmap.set_bad(PANEL)
            mesh = self.spg.pcolormesh(energy, yedges, np.ma.masked_less_equal(values, 0),
                                     cmap=cmap, norm=LogNorm(vmin=.001, vmax=maximum), shading='flat', rasterized=True)
            self.colorbar = self.figure.colorbar(mesh, ax=self.spg, pad=.02, fraction=.025)
            self.colorbar.set_label('counts/s/channel', color=MUTED, fontsize=8)
            self.colorbar.ax.tick_params(colors=MUTED, labelsize=8)
            self.spg.invert_yaxis()
            self.spg.yaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S', tz=dt.timezone.utc))
            self.count.plot(ends, rates, color=TEAL, linewidth=1.4, label='Spectrum differences')
            self.count.plot(ends, filtered, color=MUTED, linewidth=1., linestyle='--', label='Device estimator')
            self.dose.plot(ends, dose_rates, color=GOLD, linewidth=1.4)
            self.count.legend(facecolor=PANEL, edgecolor=PANEL, labelcolor=MUTED, fontsize=7, loc='upper left')
        else:
            self.spg.text(.5, .5, 'Baseline saved; waiting for a complete interval', transform=self.spg.transAxes, ha='center', color=MUTED)
        self.spg.set_xlabel('Energy (keV) • device polynomial; original channels')
        self.spg.set_ylabel('UTC • time increases downward')
        self.count.set_title('Counts over time', loc='left')
        self.count.set_ylabel('cps')
        self.dose.set_title('Device dose-rate mean over time', loc='left')
        self.dose.set_ylabel('µSv/h')
        for ax in [self.count, self.dose]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M', tz=dt.timezone.utc))
            ax.set_xlabel('UTC')
            ax.grid(alpha=.12, color=MUTED)
        self.draw_idle()


class Window(Q.QMainWindow):
    def __init__(self, inspect_run=None):
        super().__init__()
        self.setWindowTitle(f'RadiaLog {VERSION} — experiment recorder')
        self.resize(1350, 900)
        self.run = Path(inspect_run) if inspect_run else None
        self.recorder = None
        self.export_process = None
        self.export_log = None
        self.export_destination = None
        self.last_seq = None
        self.last_status = {}
        self.status_stale = False
        self.setStyleSheet(f'''
          QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
          QFrame#panel {{ background: {PANEL}; border: 1px solid #26384a; border-radius: 10px; }}
          QFrame#panel QLabel, QFrame#panel QCheckBox {{ background: transparent; }}
          QLabel#title {{ font-size: 23pt; font-weight: 600; }}
          QLabel#muted {{ color: {MUTED}; font-size: 9pt; }}
          QLabel#metric {{ font-size: 21pt; font-weight: 600; color: {TEAL}; }}
          QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox {{ background: #101c28; border: 1px solid #34495e; border-radius: 5px; padding: 6px; }}
          QPushButton {{ background: #273b4d; border: none; border-radius: 5px; padding: 9px 12px; font-weight: 600; }}
          QPushButton:hover {{ background: #354f65; }}
          QPushButton:disabled {{ color: #687b8c; background: #20303e; }}
          QPushButton#primary {{ background: {TEAL}; color: #10232a; }}
          QPushButton#stop {{ background: #78454a; }}
          QPushButton#primary:disabled, QPushButton#stop:disabled {{ color: #687b8c; background: #20303e; }}
          QPlainTextEdit {{ background: #101c28; border: 1px solid #26384a; color: {MUTED}; }}
        ''')
        main = Q.QWidget()
        self.setCentralWidget(main)
        layout = Q.QVBoxLayout(main)
        layout.setContentsMargins(24, 18, 24, 18)
        header = Q.QHBoxLayout()
        title = Q.QLabel('RadiaLog')
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
        panel = Q.QFrame()
        panel.setObjectName('panel')
        panel.setFixedWidth(300)
        settings = Q.QVBoxLayout(panel)
        settings.setContentsMargins(16, 16, 16, 16)
        settings.addWidget(Q.QLabel('EXPERIMENT'))
        self.label = Q.QLineEdit()
        self.label.setPlaceholderText('Run label / sample / conditions')
        self.serial = Q.QLineEdit('RC-103-006489')
        self.interval = Q.QSpinBox()
        self.interval.setRange(1, 600)
        self.interval.setValue(10)
        self.interval.setSuffix(' s')
        self.days = Q.QDoubleSpinBox()
        self.days.setRange(0, 365)
        self.days.setDecimals(3)
        self.days.setValue(7)
        self.days.setSuffix(' days')
        self.folder = Q.QLineEdit(str(ROOT / 'runs'))
        self.demo = Q.QCheckBox('Simulation mode (no hardware)')
        for text, widget in [('Run label', self.label), ('USB serial number', self.serial), ('Spectrum / count interval', self.interval),
                             ('Duration • 0 = continuous', self.days), ('Save runs under', self.folder)]:
            label = Q.QLabel(text)
            label.setObjectName('muted')
            settings.addWidget(label)
            settings.addWidget(widget)
        choose = Q.QPushButton('Choose folder…')
        choose.clicked.connect(self.choose_folder)
        settings.addWidget(choose)
        settings.addWidget(self.demo)
        self.start_button = Q.QPushButton('Start new recording')
        self.start_button.setObjectName('primary')
        self.start_button.clicked.connect(self.start_new)
        self.stop_button = Q.QPushButton('Stop recording')
        self.stop_button.setObjectName('stop')
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_recording)
        settings.addWidget(self.start_button)
        settings.addWidget(self.stop_button)
        open_button = Q.QPushButton('Open saved run…')
        open_button.clicked.connect(self.open_run)
        settings.addWidget(open_button)
        self.resume_button = Q.QPushButton('Resume selected run')
        self.resume_button.clicked.connect(self.resume_run)
        settings.addWidget(self.resume_button)
        self.export_button = Q.QPushButton('Export CSV + spectra…')
        self.export_button.clicked.connect(self.export)
        settings.addWidget(self.export_button)
        reveal = Q.QPushButton('Show run files')
        reveal.clicked.connect(self.reveal)
        settings.addWidget(reveal)
        settings.addStretch()
        note = Q.QLabel('Recording continues if this window closes.\nUse Stop to end the run.\n\nRetries: 1, 2, 4, 8, 16, 32, then 60 s.\nRaw spectra have no rolling buffer limit.')
        note.setObjectName('muted')
        note.setWordWrap(True)
        settings.addWidget(note)
        content.addWidget(panel)
        right = Q.QVBoxLayout()
        content.addLayout(right, 1)
        cards = Q.QHBoxLayout()
        self.metrics = []
        for text in ['Spectrum count rate', 'Device dose-rate estimator', 'Integrated dose • observed', 'Saved spectra']:
            frame = Q.QFrame()
            frame.setObjectName('panel')
            stack = Q.QVBoxLayout(frame)
            caption = Q.QLabel(text)
            caption.setObjectName('muted')
            value = Q.QLabel('—')
            value.setObjectName('metric')
            stack.addWidget(caption)
            stack.addWidget(value)
            self.metrics.append(value)
            cards.addWidget(frame)
        right.addLayout(cards)
        self.charts = Charts()
        right.addWidget(self.charts, 1)
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
        self.refresh()

    def message(self, text):
        self.log.appendPlainText(dt.datetime.now().strftime('%H:%M:%S') + '  ' + text)

    def fail(self, error):
        self.message(str(error))
        Q.QMessageBox.warning(self, 'RadiaLog', str(error))

    def choose_folder(self):
        path = Q.QFileDialog.getExistingDirectory(self, 'Save new runs under', self.folder.text())
        if path:
            self.folder.setText(path)

    def launch(self, args):
        log_path = ROOT / 'logs'
        log_path.mkdir(exist_ok=True)
        log_file = log_path / (dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.log')
        handle = log_file.open('xb')
        try:
            process = subprocess.Popen([sys.executable, '-X', 'utf8', str(ROOT / 'recorder.py'), *args],
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
                                       creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        finally:
            handle.close()
        self.recorder = process
        self.last_status = {}
        self.last_seq = None
        self.message(f'Recorder started (PID {process.pid}); diagnostics: {log_file.name}')
        self.start_button.setEnabled(False)

    def start_new(self):
        try:
            if self.active():
                raise RuntimeError('Stop the current recording before starting another')
            mode = 'demo' if self.demo.isChecked() else 'device'
            serial = 'SIMULATED' if mode == 'demo' else self.serial.text().strip()
            if not serial or any(ch in serial for ch in '<>:"/\\|?*'):
                raise ValueError('Enter a valid device serial number')
            self.run = Path(self.folder.text()).resolve() / (dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + serial)
            args = ['--run', str(self.run), '--serial', serial, '--interval', str(self.interval.value()),
                    '--seconds', str(self.days.value() * 86400), '--label', self.label.text()]
            if mode == 'demo':
                args.append('--demo')
            self.launch(args)
        except Exception as error:
            self.fail(error)

    def active(self):
        if self.recorder and self.recorder.poll() is None:
            return True
        return self.last_status.get('state') in ('starting', 'connecting', 'recording', 'reconnecting') and not self.status_stale

    def stop_recording(self):
        try:
            if not self.run or not self.last_status.get('boot_id'):
                return
            file = self.run / f"stop_{self.last_status['boot_id']}.request"
            if not file.exists():
                file.write_text('Stop requested from RadiaLog GUI\n', encoding='utf-8')
            self.message('Stop requested; waiting for the recorder to save and close')
            self.stop_button.setEnabled(False)
        except Exception as error:
            self.fail(error)

    def open_run(self):
        if self.active():
            self.fail('Stop the selected recording before switching runs; reopen this window to monitor another run')
            return
        path = Q.QFileDialog.getExistingDirectory(self, 'Select a run containing manifest.json', str(ROOT / 'runs'))
        if path:
            if not (Path(path) / 'manifest.json').exists():
                self.fail('Selected folder is not a RadiaLog run')
                return
            self.run = Path(path)
            self.last_seq = None
            self.last_status = {}
            self.refresh()

    def resume_run(self):
        try:
            if not self.run or not (self.run / 'manifest.json').exists():
                raise ValueError('Open a saved run first')
            if self.active():
                raise RuntimeError('This recording is already running')
            self.launch(['--run', str(self.run), '--resume'])
        except Exception as error:
            self.fail(error)

    def reveal(self):
        if self.run and self.run.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.run)))

    def export(self):
        try:
            if not self.run or not (self.run / 'run.sqlite').exists():
                raise ValueError('Open or record a run first')
            self.export_destination = self.run / ('export_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
            self.export_log = (ROOT / ('export_' + uuid.uuid4().hex + '.log')).open('xb')
            self.export_process = subprocess.Popen([sys.executable, '-X', 'utf8', str(ROOT / 'export_run.py'), str(self.run),
                                                    '--target', str(self.export_destination)], cwd=ROOT,
                                                   stdout=self.export_log, stderr=self.export_log, stdin=subprocess.DEVNULL,
                                                   creationflags=subprocess.CREATE_NO_WINDOW)
            self.export_button.setEnabled(False)
            self.message('Exporting a consistent snapshot of the saved run; recording can continue')
        except Exception as error:
            self.fail(error)

    def refresh(self):
        if self.export_process and self.export_process.poll() is not None:
            code = self.export_process.returncode
            self.export_log.close()
            self.export_process = None
            self.export_button.setEnabled(True)
            self.message(f'Export {"complete" if code == 0 else "failed; see export log"}: {self.export_destination}')
        if not self.run or not (self.run / 'status.json').exists():
            return
        try:
            state = json.loads((self.run / 'status.json').read_text(encoding='utf-8'))
            age = dt.datetime.now(dt.timezone.utc).timestamp() - timestamp(state['utc'])
            self.status_stale = age > 25 and state['state'] in ('starting', 'connecting', 'recording', 'reconnecting')
            self.last_status = state
            active = self.active()
            prefix = 'SIMULATED • ' if state['mode'] == 'demo' else ''
            name = 'STATUS STALE — recorder may have stopped' if self.status_stale else state['state'].upper()
            self.connection.setText(prefix + name)
            self.start_button.setEnabled(not active)
            self.stop_button.setEnabled(active)
            self.resume_button.setEnabled(not active)
            self.metrics[3].setText(f"{state.get('snapshots', 0):,}")
            snapshot = state.get('last_snapshot')
            interval = state.get('last_interval', {})
            if snapshot:
                rate = interval.get('count_rate_cps')
                self.metrics[0].setText(f'{rate:.2f} cps' if rate is not None else 'Baseline')
                rt = snapshot.get('realtime') or {}
                dose = rt.get('dose_rate')
                self.metrics[1].setText(f'{dose * DOSE_R_TO_USV:.3f} µSv/h' if dose is not None else '—')
                rare = snapshot.get('accumulated_dose') or {}
                experiment_dose = snapshot.get('derived_observed_dose_uSv')
                self.metrics[2].setText(f'{experiment_dose:.4f} µSv' if experiment_dose is not None else 'Waiting')
                coverage = snapshot.get('derived_observed_dose_coverage', 0)
                self.metrics[2].setToolTip(f'Derived from measured dose rate; observed time coverage {coverage:.2%}. Missing periods are excluded. Device counter and its sample age are saved separately.')
                seq = snapshot['seq']
                if self.last_seq != (str(self.run), seq):
                    db = readonly(self.run / 'run.sqlite')
                    try:
                        entries = list(db.execute('SELECT utc,epoch,derived,file FROM snapshots ORDER BY seq DESC LIMIT 241'))
                    finally:
                        db.close()
                    rows = [(utc, epoch, json.loads(derived), json.loads((self.run / file).read_text(encoding='utf-8')))
                            for utc, epoch, derived, file in reversed(entries)]
                    self.charts.show_rows(rows, snapshot, state['mode'])
                    self.last_seq = (str(self.run), seq)
                    self.message(f'Saved spectrum {seq}; ' + ', '.join(interval.get('quality', [])))
            retry = f" • retry in {state.get('retry_delay_s')} s" if state['state'] == 'reconnecting' else ''
            saved_snapshot = state.get('last_snapshot') or {}
            coverage = saved_snapshot.get('derived_observed_dose_coverage')
            coverage_text = f' • dose time coverage: {coverage:.2%}' if coverage is not None else ''
            self.detail.setText(f"{self.run}\n{state.get('message', '')}{retry} • retries: {state.get('retries', 0)}{coverage_text}")
            cm = state.get('clock_measurement') or {}
            timing = f"SNTP {cm.get('server')} • offset {cm['offset_s']:+.3f} s • estimated ±{cm['estimated_uncertainty_s']:.3f} s" if cm.get('verified') else 'UTC accuracy unverified • host clock fallback'
            self.footer.setText('Raw counts preserved • dose uses the device R→Sv convention • ' + timing)
        except (OSError, ValueError, sqlite3.Error, KeyError) as error:
            self.detail.setText(f'Waiting for saved state: {error}')

    def closeEvent(self, event):
        # Recorder has its own process and saved control/status files; window closure
        # does not issue Stop or delete files. A later window can Open saved run.
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inspect-run', type=Path)
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    app = Q.QApplication(sys.argv[:1])
    # Explicit font bytes also make offscreen rendering reproducible in restricted
    # Windows sessions whose Qt platform plugin cannot enumerate system fonts.
    for name in ['segoeui.ttf', 'segoeuib.ttf']:
        font = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / name
        if font.exists():
            QtGui.QFontDatabase.addApplicationFontFromData(QtCore.QByteArray(font.read_bytes()))
    app.setFont(QtGui.QFont('Segoe UI', 10))
    app.setStyle('Fusion')
    window = Window(args.inspect_run)
    window.show()
    if args.screenshot:
        def grab():
            window.refresh()
            window.charts.draw()
            if not window.grab().save(str(args.screenshot)):
                raise RuntimeError('Screenshot save failed')
            app.quit()
        QtCore.QTimer.singleShot(1200, grab)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
