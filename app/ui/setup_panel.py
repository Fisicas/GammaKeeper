"""New-run form, start/stop/resume/open controls and run-settings restore."""
import datetime as dt
from pathlib import Path
import subprocess
import json

from PySide6 import QtCore, QtGui, QtWidgets as Q
from analysis import timestamp
from runtime import python_command
from winfiles import process_alive
from run_settings import load_run_settings, run_summary
from run_policy import require_resume
from time_input import TimeInput
from ui.paths import DATA_ROOT, ROOT


class SettingsSpinBox(Q.QDoubleSpinBox):
    def textFromValue(self, value):
        return f'{value:.12g}'


NO_DEVICE_HELP = ('No RadiaCode was found on USB. Check that it is switched on, connected with a data (not charge-only) '
                  'cable, and that the RadiaCode app is closed; then click Detect again.')


class SetupPanelMixin:
    def _build_setup_panel(self, content):
        """Left panel: new-run form and run controls. Returns its layout."""
        panel = Q.QFrame()
        panel.setObjectName('panel')
        panel.setFixedWidth(300)
        settings = Q.QVBoxLayout(panel)
        settings.setContentsMargins(16, 16, 16, 16)
        self.settings_heading = Q.QLabel('NEW RECORDING')
        settings.addWidget(self.settings_heading)
        self.label = Q.QLineEdit()
        self.label.setPlaceholderText('Run label / sample / conditions')
        self.serial = Q.QLineEdit()
        self.serial.setPlaceholderText('Detector serial, e.g. RC-103-000000')
        self.detect_button = Q.QPushButton('Detect')
        self.detect_button.setToolTip('Find RadiaCode detectors connected by USB (read-only; safe during a recording)')
        self.detect_button.clicked.connect(lambda: self.detect_device(interactive=True))
        serial_row = Q.QWidget()
        serial_layout = Q.QHBoxLayout(serial_row)
        serial_layout.setContentsMargins(0, 0, 0, 0)
        serial_layout.addWidget(self.serial, 1)
        serial_layout.addWidget(self.detect_button)
        self.interval = TimeInput('s')
        self.interval.setDecimals(9)
        self.interval.setRange(1, 86400)
        self.interval.setValue(10)
        self.interval.setSuffix(' s')
        self.days = TimeInput('days', continuous=True)
        self.days.setRange(0, 365)
        self.days.setDecimals(9)
        self.days.setValue(7)
        self.days.setSuffix(' days')
        self.folder = Q.QLineEdit(str(DATA_ROOT))
        self.demo = Q.QCheckBox('Simulation mode (no hardware)')
        self.monitor_enabled=Q.QCheckBox('Monitor after this window closes');self.monitor_enabled.setChecked(True)
        self.backup_parent=Q.QLineEdit();self.backup_parent.setPlaceholderText('Optional daily backup folder')
        self.backup_choose=Q.QPushButton('Choose daily backup folder…')
        self.backup_choose.clicked.connect(self.choose_backup_parent)
        self.ten_day_preset=Q.QPushButton('10-day qualification • 10 s')
        self.ten_day_preset.clicked.connect(lambda:(self.days.setValue(10),self.interval.setValue(10)))
        for text, widget in [('Run label', self.label), ('USB serial number', serial_row), ('Spectrum / count interval', self.interval),
                             ('Duration • 0 = continuous', self.days), ('Save runs under', self.folder)]:
            label = Q.QLabel(text)
            label.setObjectName('muted')
            settings.addWidget(label)
            settings.addWidget(widget)
        choose = Q.QPushButton('Choose folder…')
        choose.clicked.connect(self.choose_folder)
        self.choose_button = choose
        settings.addWidget(choose)
        settings.addWidget(self.demo)
        for widget in (self.ten_day_preset,self.monitor_enabled,self.backup_parent,self.backup_choose):settings.addWidget(widget)
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
        self.resume_button.setEnabled(False)
        settings.addWidget(self.resume_button)
        self.setup_button = Q.QPushButton('Set up new recording')
        self.setup_button.clicked.connect(self.prepare_new)
        self.setup_button.setEnabled(False)
        settings.addWidget(self.setup_button)
        self.setup_fields = (self.label, self.serial, self.interval, self.days,
                              self.folder, self.demo, self.choose_button, self.detect_button,self.monitor_enabled,self.backup_parent,self.backup_choose,self.ten_day_preset)
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
        scroll=Q.QScrollArea();scroll.setWidgetResizable(True);scroll.setFixedWidth(320)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(panel);content.addWidget(scroll)
        return settings

    def choose_folder(self):
        path = Q.QFileDialog.getExistingDirectory(self, 'Save new runs under', self.folder.text())
        if path:
            self.folder.setText(path)

    def choose_backup_parent(self):
        path=Q.QFileDialog.getExistingDirectory(self,'Choose optional daily backup folder',self.backup_parent.text())
        if path:self.backup_parent.setText(path)

    def restore_run_settings(self, force=False):
        if not self.run:
            return
        selected = str(self.run.resolve())
        if force or self.settings_run != selected:
            self.science_cancel.set();self.science_result=None;self._last_science=-float('inf')
            self.science_figure.clear();self.science_canvas.draw_idle()
            self.accumulation_cancel.set();self.accumulation_key=None;self.accumulation_engine=None;self.accumulation_row=None
            self.selected_analysis_row=None
            self._spectrum_key=None
            self.spectrum_canvas.empty('Loading recorded spectra…')
            # Validate everything before populating any fields. A bad manifest
            # must never leave the selected run represented by new-run defaults.
            self.settings_run = None
            self.run_settings = None
            for widget in self.setup_fields:
                widget.setEnabled(False)
            self.settings_heading.setText('SELECTED RUN')
            self.start_button.setEnabled(False)
            self.run_summary.setText('Saved run settings unavailable; checking manifest…')
            self.run_settings = load_run_settings(self.run)
            saved = self.run_settings
            self.label.setText(saved['label'])
            self.serial.setText(saved['serial'])
            self.interval.setValue(saved['interval_s'])
            self.days.setMaximum(max(365., saved['duration_s'] / 86400))
            self.days.setValue(saved['duration_s'] / 86400)
            self.folder.setText(str(self.run.resolve().parent))
            self.demo.setChecked(saved['mode'] == 'demo')
            from experiment_metadata import monitoring
            monitor=monitoring(self.run)
            self.monitor_enabled.setChecked(monitor['enabled']);self.backup_parent.setText(monitor['backup_parent'])
            self.settings_run = selected
            begin=timestamp(saved['created_utc'])
            end=timestamp(saved['planned_end_utc']) if saved.get('planned_end_utc') else dt.datetime.now(dt.timezone.utc).timestamp()
            self.view_from.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(int(begin),QtCore.QTimeZone(0)))
            self.view_until.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(int(end),QtCore.QTimeZone(0)))
            self.collection_from.setDateTime(QtCore.QDateTime.fromMSecsSinceEpoch(int(begin*1000),QtCore.QTimeZone(0)))
            self.collection_until.setDateTime(QtCore.QDateTime.fromMSecsSinceEpoch(int(end*1000),QtCore.QTimeZone(0)))
            self.export_from.setDateTime(QtCore.QDateTime.fromMSecsSinceEpoch(int(begin*1000),QtCore.QTimeZone(0)))
            self.export_until.setDateTime(QtCore.QDateTime.fromMSecsSinceEpoch(int(end*1000),QtCore.QTimeZone(0)))
            self.export_time_zero.setDateTime(QtCore.QDateTime.fromMSecsSinceEpoch(int(begin*1000),QtCore.QTimeZone(0)))
            self.update_time_export()
            self.run_summary.setToolTip(
                f"Saved start UTC: {saved['created_utc']}\n"
                f"Saved planned end UTC: {saved.get('planned_end_utc') or 'none'}\n"
                'Resume keeps this original schedule; it does not restart the duration.\n'
                'Displayed dates include the local UTC offset.')
        now=dt.datetime.fromtimestamp(self.current_timeline_seconds(),dt.timezone.utc)
        self.run_summary.setText(run_summary(self.run_settings,now))

    def prepare_new(self):
        """Leave a stopped saved run and use its values as a new-run template."""
        if getattr(self,'read_only',False):return
        if self.active():
            self.fail('Stop the selected recording before setting up a new recording')
            return
        self.run = None
        self.science_cancel.set();self.science_result=None;self._last_science=-float('inf')
        self.science_figure.clear();self.science_canvas.draw_idle()
        self.experiment_overview.setText('Configure a new recording.');self.science_text.clear()
        self.update_time_export()
        self.preview_cancel.set();self.analysis_rows=[];self.preview_key=None;self.preview_summary={}
        self.selected_analysis_row=None;self._spectrum_key=None
        self.accumulation_cancel.set();self.accumulation_key=None;self.accumulation_engine=None;self.accumulation_row=None
        self.spectrum_canvas.empty('Start a new recording or open a saved run to view interval spectra.')
        self.recorder = None
        self.last_status = {}
        self.last_seq = None
        self.settings_run = None
        self.run_settings = None
        self.telemetry_run = None
        self.telemetry_reader = None
        self.settings_heading.setText('NEW RECORDING')
        for widget in self.setup_fields:
            widget.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        self.setup_button.setEnabled(False)
        self.connection.setText('Ready')
        self.run_summary.setText('Configure a new recording. Values copied from the previously selected run.')
        self.run_summary.setToolTip('')
        for metric in self.metrics:
            metric.setText('—')
        if self.charts.colorbar:
            self.charts.colorbar.remove()
            self.charts.colorbar = None
        for axis in (self.charts.spg, self.charts.count, self.charts.dose):
            axis.clear()
        self.charts.empty()
        self.health.setText('Waiting for a new recording')
        self.detail.setText('Starting a new recording creates a separate run folder.')

    def launch(self, args):
        log_path = ROOT / 'logs'
        log_path.mkdir(exist_ok=True)
        log_file = log_path / (dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.log')
        self.last_launch_log = log_file
        handle = log_file.open('xb')
        try:
            process = subprocess.Popen([python_command(), '-X', 'utf8', str(ROOT / 'supervisor.py'), *args],
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=handle, stderr=handle,
                                       creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        finally:
            handle.close()
        self.recorder = process
        self.last_status = {}
        self.last_seq = None
        self.message(f'Recorder started (PID {process.pid}); diagnostics: {log_file.name}')
        self.connection.setText('PREPARING RECORDING • checking clock and settings')
        self.start_button.setEnabled(False)

    def start_new(self):
        if getattr(self,'read_only',False):return
        try:
            if self.settings_run:
                raise RuntimeError('Choose Set up new recording to edit settings and create a separate run')
            if self.active():
                raise RuntimeError('Stop the current recording before starting another')
            mode = 'demo' if self.demo.isChecked() else 'device'
            serial = 'SIMULATED' if mode == 'demo' else self.serial.text().strip()
            if mode == 'device' and self._device_checks_enabled():
                serial = self.confirm_device(serial)
                if serial is None:
                    return
            if not serial or any(ch in serial for ch in '<>:"/\\|?*'):
                raise ValueError('Enter a valid device serial number')
            from run_policy import validate_schedule
            interval, seconds = self.interval.seconds(), self.days.seconds()
            validate_schedule(interval, seconds)
            self.run = Path(self.folder.text()).resolve() / (dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '_' + serial)
            args = ['--run', str(self.run), '--serial', serial, '--interval', str(interval),
                    '--seconds', str(seconds), '--label', self.label.text()]
            backup=self.backup_parent.text().strip()
            if backup and not Path(backup).is_dir():raise ValueError('Choose an existing backup parent directory')
            args += ['--monitor-config',json.dumps(dict(enabled=self.monitor_enabled.isChecked(),notifications=True,
                backup_parent=str(Path(backup).resolve()) if backup else '',backup_interval_s=86400,disconnect_warn_s=120))]
            if mode == 'demo':
                args.append('--demo')
            self.launch(args)
            if hasattr(self, 'save_preferences'):
                self.save_preferences()  # remember this detector and setup even if the window never closes cleanly
        except Exception as error:
            self.fail(error)

    def _device_checks_enabled(self):
        """USB detection and confirmations; off in test mode so tests never see real hardware or block on dialogs."""
        import gui_preferences
        return not gui_preferences.test_mode()

    def detect_device(self, interactive=False):
        """Fill the serial field from the RadiaCode(s) on USB; returns the chosen serial or None."""
        from device_discovery import connected_serials
        found = connected_serials()
        current = self.serial.text().strip().upper()
        if not found:
            if interactive:
                self.fail(NO_DEVICE_HELP)
            return None
        if current in found:
            chosen = current
        elif len(found) == 1:
            chosen = found[0]
        elif interactive:
            chosen, ok = Q.QInputDialog.getItem(self, 'Choose detector', 'Several RadiaCode detectors are connected:', found, 0, False)
            if not ok:
                return None
        else:
            self.message('Several RadiaCode detectors are connected (' + ', '.join(found) + '); click Detect to choose one')
            return None
        if chosen != self.serial.text().strip():
            self.serial.setText(chosen)
            self.message(f'RadiaCode {chosen} detected on USB')
        elif interactive:
            self.message(f'RadiaCode {chosen} is connected')
        return chosen

    def confirm_device(self, serial):
        """Check the serial against the detectors on USB before starting; None cancels the start."""
        from device_discovery import connected_serials
        found = connected_serials()
        if not serial:
            if not found:
                raise ValueError(NO_DEVICE_HELP)
            return self.detect_device(interactive=True)
        if serial.upper() in found:
            return serial
        Yes = Q.QMessageBox.StandardButton.Yes
        if found:
            replacement = found[0] if len(found) == 1 else None
            if replacement and Q.QMessageBox.question(
                    self, 'Detector not connected',
                    f'{serial} is not connected, but {replacement} is.\n\nRecord from {replacement} instead?') == Yes:
                self.serial.setText(replacement)
                return replacement
        if Q.QMessageBox.question(
                self, 'Detector not detected',
                f'{serial} is not currently detected on USB.\n\nStart anyway? The recorder keeps retrying until it is '
                'connected, and the gap is recorded.\n\n' + NO_DEVICE_HELP) != Yes:
            return None
        return serial

    def active(self):
        if self.run and (self.run/'BACKUP_PLAN.json').exists():return False
        if self.recorder and self.recorder.poll() is None:
            return True
        sup = self.last_status.get('supervisor') or {}
        # After a Windows restart a reused PID must not make a dead run look active (finding F3).
        from system_time import boot_identity
        if any(record.get('system_boot_id') and record['system_boot_id'] != boot_identity() for record in (self.last_status, sup)):
            return False
        if sup.get('status') in ('starting','running','restart_wait','stopping'):
            try:
                if dt.datetime.now(dt.timezone.utc).timestamp()-timestamp(sup['utc'])<6:
                    return True  # Fresh journal heartbeat also works when process queries are unavailable.
            except (KeyError,ValueError):
                pass
        if sup.get('status') in ('starting','running','restart_wait','stopping') and process_alive(sup['pid']):
            return True
        return (self.last_status.get('state') in ('starting','connecting','recording','reconnecting')
                and process_alive(self.last_status.get('pid',0)))

    def stop_recording(self):
        if getattr(self,'read_only',False):return
        try:
            if not self.run or not self.last_status.get('boot_id'):
                return
            if Q.QMessageBox.question(self,'Stop recording',
                'Stop the selected acquisition? Saved data will be preserved.\n\n'+self.run_summary.text(),
                Q.QMessageBox.StandardButton.Yes|Q.QMessageBox.StandardButton.No,
                Q.QMessageBox.StandardButton.No)!=Q.QMessageBox.StandardButton.Yes:return
            file = Path(self.last_status.get('stop_request') or self.run / f"stop_{self.last_status['boot_id']}.request")
            if file.resolve().parent != self.run.resolve():
                raise ValueError('Invalid stop-control path')
            if not file.exists():
                file.write_text('Stop requested from GammaKeeper GUI\n', encoding='utf-8')
            self.message('Stop requested; waiting for the recorder to save and close')
            self.stop_button.setEnabled(False)
        except Exception as error:
            self.fail(error)

    def open_run(self):
        if self.active() and not getattr(self,'read_only',False):
            self.fail('Stop the selected recording before switching runs; reopen this window to monitor another run')
            return
        path = Q.QFileDialog.getExistingDirectory(self, 'Select a run containing manifest.json', str(DATA_ROOT))
        if path:
            if not (Path(path) / 'manifest.json').exists():
                self.fail('Selected folder is not a recorded run (no manifest.json)')
                return
            self.run = Path(path)
            self.last_seq = None
            self.last_status = {}
            self.refresh()

    def resume_run(self):
        if getattr(self,'read_only',False):return
        try:
            if not self.run or not (self.run / 'manifest.json').exists():
                raise ValueError('Open a saved run first')
            if self.active():
                raise RuntimeError('This recording is already running')
            self.restore_run_settings(force=True)
            require_resume(self.run)
            self.launch(['--run', str(self.run), '--resume'])
        except Exception as error:
            self.fail(error)

    def reveal(self):
        if self.run and self.run.exists():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.run)))
