"""An offline export panel; jobs run independently of acquisition and rendering."""
import datetime as dt
from pathlib import Path
from PySide6 import QtWidgets as Q, QtCore, QtGui
from time_input import TimeInput
from time_resolved import aligned_count
from run_settings import load_run_settings


def install_time_export(window):
    page = Q.QWidget()
    layout = Q.QVBoxLayout(page)
    heading = Q.QLabel('Build a time-resolved spectral series from the saved raw records')
    heading.setStyleSheet('font-size: 16pt; font-weight: 600;')
    layout.addWidget(heading)
    explanation = Q.QLabel('Keep the original intervals for the fastest available timing, or combine complete intervals into wider spectra. '
                           'Exports retain actual start/end times and measured exposure. Acquisition continues independently.')
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    form = Q.QFormLayout()
    window.export_mode = Q.QComboBox()
    for label, mode in (('Original recorded intervals', 'native'), ('Nonoverlapping bins by duration', 'width'),
                        ('Choose number of spectra', 'number'), ('Moving average (correlated windows)', 'sliding')):
        window.export_mode.addItem(label, mode)
    window.export_width = TimeInput()
    window.export_width.setRange(1, 31536000)
    window.export_width.setValue(60)
    window.export_step = TimeInput()
    window.export_step.setRange(1, 31536000)
    window.export_step.setValue(10)
    window.export_number = Q.QSpinBox()
    window.export_number.setRange(1, 1000000)
    window.export_number.setValue(100)
    window.export_range = Q.QCheckBox('Restrict to this UTC collection range')
    window.export_from, window.export_until = Q.QDateTimeEdit(), Q.QDateTimeEdit()
    for edit in (window.export_from, window.export_until):
        edit.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        edit.setTimeZone(QtCore.QTimeZone(0))
        edit.setCalendarPopup(True)
    window.export_latest = Q.QCheckBox('Through latest saved spectrum')
    window.export_latest.setChecked(True)
    window.export_custom_zero = Q.QCheckBox('Use an explicit experiment time zero')
    window.export_zero_source = Q.QComboBox()
    for label, value in (('Seal time when available (default)', 'auto'),
                         ('Current seal annotation (required)', 'seal'),
                         ('First included acquisition interval', 'acquisition')):
        window.export_zero_source.addItem(label, value)
    window.export_keep_aggregates = Q.QCheckBox('Keep recovered aggregates as variable-width observations')
    window.export_keep_aggregates.setToolTip('Never splits counts into invented time bins. Requires bin-integrated fitting.')
    window.export_time_zero = Q.QDateTimeEdit()
    window.export_time_zero.setDisplayFormat('yyyy-MM-dd HH:mm:ss.zzz')
    window.export_time_zero.setTimeZone(QtCore.QTimeZone(0))
    window.export_time_zero.setCalendarPopup(True)
    window.export_epoch = Q.QSpinBox()
    window.export_epoch.setRange(-1, 1000000)
    window.export_epoch.setValue(-1)
    window.export_epoch.setSpecialValueText('All (separate segments)')
    window.export_energy_range = Q.QCheckBox('Restrict fitting matrices to energy range (full spectra remain in NPZ / RCSPG)')
    window.export_energy_min, window.export_energy_max = Q.QDoubleSpinBox(), Q.QDoubleSpinBox()
    for spin in (window.export_energy_min, window.export_energy_max):
        spin.setRange(-10000, 10000)
        spin.setDecimals(3)
        spin.setSuffix(' keV')
    window.export_energy_min.setValue(0)
    window.export_energy_max.setValue(3000)
    window.export_xlsx = Q.QCheckBox('Also write UFit-compatible XLSX (may take longer for large matrices)')
    window.export_note = Q.QLabel()
    window.export_note.setWordWrap(True)
    window.export_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    form.addRow('Time bins', window.export_mode)
    form.addRow('Bin / averaging duration', window.export_width)
    form.addRow('Moving-window step', window.export_step)
    form.addRow('Number of spectra', window.export_number)
    form.addRow('Epoch', window.export_epoch)
    form.addRow(window.export_range)
    form.addRow('UTC start', window.export_from)
    form.addRow('UTC end', window.export_until)
    form.addRow(window.export_latest)
    form.addRow(window.export_custom_zero)
    form.addRow('Time-zero evidence', window.export_zero_source)
    form.addRow(window.export_keep_aggregates)
    zero_row = Q.QHBoxLayout()
    zero_row.addWidget(window.export_time_zero, 1)
    window.export_use_seal = Q.QPushButton('Use seal time')
    window.export_use_seal.setToolTip('Set time zero to the current seal note from the Notes tab.')
    window.export_use_seal.clicked.connect(lambda: use_seal_time(window))
    zero_row.addWidget(window.export_use_seal)
    form.addRow('Experiment time zero (UTC)', zero_row)
    form.addRow(window.export_energy_range)
    form.addRow('Fitting energy from', window.export_energy_min)
    form.addRow('to', window.export_energy_max)
    form.addRow(window.export_xlsx)
    layout.addLayout(form)
    layout.addWidget(window.export_note)
    window.export_series_button = Q.QPushButton('Export time-resolved spectra…')
    layout.addWidget(window.export_series_button)
    window.export_series_result = Q.QLabel('A new output folder contains CSV / NPZ, bin metadata and nonoverlapping RCSPG. Choose XLSX for UFit. '
                                           'Time zero is the first included interval start; no kinetic model is fitted.')
    window.export_series_result.setWordWrap(True)
    window.export_series_result.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    layout.addWidget(window.export_series_result)
    actions = Q.QHBoxLayout()
    window.export_open_folder = Q.QPushButton('Open last export folder')
    window.export_open_log = Q.QPushButton('Open export log')
    window.export_open_folder.setEnabled(False)
    window.export_open_log.setEnabled(False)
    actions.addWidget(window.export_open_folder)
    actions.addWidget(window.export_open_log)
    actions.addStretch()
    layout.addLayout(actions)
    layout.addStretch()
    scroll = Q.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    scroll.setFrameShape(Q.QFrame.Shape.NoFrame)
    window.export_scroll = scroll
    window.tabs.addTab(scroll, 'Time-resolved export')

    def update(*unused):
        mode = window.export_mode.currentData()
        window.export_width.setEnabled(mode in ('width', 'sliding'))
        window.export_step.setEnabled(mode == 'sliding')
        window.export_number.setEnabled(mode == 'number')
        window.export_from.setEnabled(window.export_range.isChecked())
        window.export_until.setEnabled(window.export_range.isChecked() and not window.export_latest.isChecked())
        window.export_latest.setEnabled(window.export_range.isChecked())
        window.export_time_zero.setEnabled(window.export_custom_zero.isChecked() and window.export_zero_source.currentData()!='seal')
        for control in (window.export_energy_min, window.export_energy_max):
            control.setEnabled(window.export_energy_range.isChecked())
        try:
            if not window.run:
                raise ValueError('Open a saved run or start a recording to export spectra.')
            saved = load_run_settings(window.run)
            cadence = saved['interval_s']
            note = f'Saved cadence: {cadence:g} s. Finer spectra cannot be recovered from these records. '
            if mode in ('width', 'sliding'):
                width = aligned_count(window.export_width.seconds(), cadence, 'Width')
                note += f'Each full spectrum sums {width} recorded intervals; actual durations are exported. '
            if mode == 'sliding':
                step = aligned_count(window.export_step.seconds(), cadence, 'Step')
                if step > width:
                    raise ValueError('Moving-window step must not exceed width.')
                if step < width:
                    note += 'Overlapping windows share counts: errors are correlated. RCSPG is omitted; use independent bins for ordinary decay fitting. '
                note += 'Incomplete trailing windows are not exported. '
            elif mode == 'number':
                note += 'Requires one contiguous segment; bins may contain different numbers of intervals. '
            elif mode == 'width':
                note += 'The final bin may be shorter; its exposure is preserved. '
            import annotations
            current = annotations.seal(annotations.load(window.run))
            zero_basis = window.export_zero_source.currentData()
            if window.export_custom_zero.isChecked() and zero_basis!='seal': zero_basis = 'custom UTC'
            note += ('Recovered aggregates are isolated variable-width observations. ' if window.export_keep_aggregates.isChecked()
                     else 'Recovered aggregates remain in the exclusions file. ')
            if window.export_keep_aggregates.isChecked() and mode=='sliding':
                raise ValueError('Use original or nonoverlapping bins to retain recovered aggregates.')
            if zero_basis in ('auto','seal') and current:
                note += f"Time zero: seal note {current['id']} at {annotations.iso(current['utc'])} UTC. "
            elif zero_basis=='seal':
                raise ValueError('No seal annotation; record the sealing event in Notes first.')
            elif zero_basis=='auto':
                note += 'No seal annotation: acquisition start will be used; it is not experimental sealing. '
            else:
                note += 'Time zero: '+zero_basis+'. '
            note += 'Resets and suspect timing remain explicitly excluded.'
            window.export_note.setText(note)
            window.export_series_button.setEnabled(not any(job['script']=='time_resolved.py' and job['process'].poll() is None for job in window.background_jobs))
        except (ValueError, OSError, KeyError, TypeError) as error:
            window.export_note.setText(str(error))
            window.export_series_button.setEnabled(False)

    def export():
        try:
            if not window.run:
                raise ValueError('Select a recorded run first')
            # Validate before asking for an output location or starting a job.
            saved = load_run_settings(window.run)
            mode = window.export_mode.currentData()
            extra = ['--mode', mode]
            custom_zero = window.export_custom_zero.isChecked() and window.export_zero_source.currentData()!='seal'
            extra += ['--time-zero-basis', 'custom' if custom_zero else window.export_zero_source.currentData()]
            if window.export_keep_aggregates.isChecked():
                extra += ['--keep-aggregates']
            if mode in ('width', 'sliding'):
                width = window.export_width.seconds()
                aligned_count(width, saved['interval_s'], 'Width')
                extra += ['--width', str(width)]
            if mode == 'sliding':
                step = window.export_step.seconds()
                aligned_count(step, saved['interval_s'], 'Step')
                if step > width:
                    raise ValueError('Moving-window step must not exceed width')
                extra += ['--step', str(step)]
            if mode == 'number':
                extra += ['--number', str(window.export_number.value())]
            if window.export_range.isChecked():
                start = window.export_from.dateTime().toMSecsSinceEpoch() / 1000
                extra += ['--start-utc', str(start)]
                if not window.export_latest.isChecked():
                    end = window.export_until.dateTime().toMSecsSinceEpoch() / 1000
                    if start >= end:
                        raise ValueError('UTC start must precede end')
                    extra += ['--end-utc', str(end)]
            if window.export_epoch.value() >= 0:
                extra += ['--epoch', str(window.export_epoch.value())]
            if window.export_xlsx.isChecked():
                extra += ['--xlsx']
            if custom_zero:
                extra += ['--time-zero-utc', str(window.export_time_zero.dateTime().toMSecsSinceEpoch() / 1000)]
            if window.export_energy_range.isChecked():
                lo, hi = window.export_energy_min.value(), window.export_energy_max.value()
                if lo >= hi:
                    raise ValueError('Energy start must precede end')
                extra += ['--energy-min-kev', str(lo), '--energy-max-kev', str(hi)]
            parent = Q.QFileDialog.getExistingDirectory(window, 'Save new time-resolved export under', str(window.run))
            if not parent:
                return
            target = Path(parent) / ('time_resolved_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
            extra += ['--target', str(target)]
            window.run_job('time_resolved.py', target, *extra)
            window.last_time_export = window.background_jobs[-1] if window.background_jobs else None
            if window.last_time_export:
                window.export_open_folder.setEnabled(True)
                window.export_open_log.setEnabled(True)
            update()
            window.export_series_result.setText(f'Export running independently. Output: {target}\nOnly use an output with COMPLETE.json; errors appear in Events / logs and recorder diagnostics.')
        except Exception as error:
            window.fail(error)

    for control in (window.export_mode, window.export_range, window.export_latest, window.export_energy_range,
                    window.export_custom_zero, window.export_zero_source, window.export_keep_aggregates):
        signal = control.currentIndexChanged if isinstance(control, Q.QComboBox) else control.toggled
        signal.connect(update)
    for control in (window.export_width, window.export_step):
        control.edit.textChanged.connect(update)
        control.units.currentTextChanged.connect(update)
    window.export_series_button.clicked.connect(export)
    def open_result(kind):
        job = getattr(window, 'last_time_export', None)
        if job:
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(job[kind])))
    window.export_open_folder.clicked.connect(lambda: open_result('target'))
    window.export_open_log.clicked.connect(lambda: open_result('log'))
    window.tabs.currentChanged.connect(lambda index: update() if window.tabs.widget(index) is scroll else None)
    window.update_time_export = update
    update()


def use_seal_time(window):
    """Time zero = the run's current seal note (Notes tab)."""
    import annotations
    current = annotations.seal(annotations.load(window.run)) if window.run else None
    if not current:
        window.fail('No seal time recorded for this run; mark it in the Notes tab first')
        return
    window.export_custom_zero.setChecked(True)
    window.export_zero_source.setCurrentIndex(window.export_zero_source.findData('seal'))
    window.export_time_zero.setDateTime(QtCore.QDateTime.fromMSecsSinceEpoch(int(round(current['utc'] * 1000)), QtCore.QTimeZone(0)))
    window.message(f"Time zero set to the seal time {annotations.iso(current['utc'])} UTC")
