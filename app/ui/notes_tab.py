"""Notes tab: mark the seal time (time zero) and record experiment notes (plan S1)."""
import datetime as dt
import sqlite3

from PySide6 import QtCore, QtWidgets as Q

import annotations
from system_time import boot_identity


class NoteDialog(Q.QDialog):
    """Kind, text and event time of a new note; 'now' means the moment the dialog opened."""

    def __init__(self, parent, now_utc):
        super().__init__(parent)
        self.setWindowTitle('Add note')
        self.now_utc = now_utc
        form = Q.QFormLayout(self)
        self.kind = Q.QComboBox()
        for kind, label in annotations.KINDS:
            self.kind.addItem(label, kind)
        self.kind.setCurrentIndex(self.kind.findData('note'))
        self.text = Q.QLineEdit()
        self.text.setPlaceholderText('e.g. charcoal 25.0 g, canister 3, exposed 72 h in basement')
        self.uncertainty = Q.QDoubleSpinBox()
        self.uncertainty.setRange(0, 86400)
        self.uncertainty.setSuffix(' s')
        self.uncertainty_known = Q.QCheckBox('Event-time uncertainty estimated')
        self.uncertainty_known.toggled.connect(self.uncertainty.setEnabled)
        self.uncertainty.setEnabled(False)
        local = dt.datetime.fromtimestamp(now_utc).astimezone()
        self.at_now = Q.QRadioButton(f'Now ({local:%H:%M:%S %Z})')
        self.at_time = Q.QRadioButton('At')
        self.at_now.setChecked(True)
        self.when = Q.QDateTimeEdit(QtCore.QDateTime.fromMSecsSinceEpoch(int(now_utc * 1000)))
        self.when.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
        self.when.setCalendarPopup(True)
        self.when.setEnabled(False)
        self.at_time.toggled.connect(self.when.setEnabled)
        when = Q.QHBoxLayout()
        when.addWidget(self.at_now)
        when.addWidget(self.at_time)
        when.addWidget(self.when, 1)
        form.addRow('Kind', self.kind)
        form.addRow('Note', self.text)
        form.addRow('Event time (local)', when)
        form.addRow(self.uncertainty_known, self.uncertainty)
        buttons = Q.QDialogButtonBox(Q.QDialogButtonBox.StandardButton.Ok | Q.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        """(kind, text, utc, entered_by_user)."""
        if self.at_time.isChecked():
            return self.kind.currentData(), self.text.text().strip(), self.when.dateTime().toMSecsSinceEpoch() / 1000, True
        return self.kind.currentData(), self.text.text().strip(), self.now_utc, False


class NotesMixin:
    def _build_notes_tab(self):
        page = Q.QWidget()
        layout = Q.QVBoxLayout(page)
        buttons = Q.QHBoxLayout()
        self.seal_button = Q.QPushButton('Mark seal time now')
        self.seal_button.setObjectName('primary')
        self.seal_button.setToolTip('Record this moment as the canister seal time: time zero for decay analysis.')
        self.seal_button.clicked.connect(self.mark_seal)
        self.note_button = Q.QPushButton('Add note…')
        self.note_button.clicked.connect(self.add_note)
        for widget in (self.seal_button, self.note_button):
            buttons.addWidget(widget)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.seal_summary = Q.QLabel('No seal time recorded.')
        self.seal_summary.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.seal_summary)
        self.notes_table = Q.QTableWidget(0, 5)
        self.notes_table.setHorizontalHeaderLabels(['Event time (local)', 'UTC', 'Kind', 'Note', 'Time basis'])
        self.notes_table.setEditTriggers(Q.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.notes_table.horizontalHeader().setSectionResizeMode(3, Q.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.notes_table, 1)
        info = Q.QLabel('Notes are stored beside the run in annotations.sqlite and cannot be edited or deleted. '
                        'Marking the seal again adds a newer seal note; the latest one is time zero. '
                        'Times use the recorder\'s SNTP reference when available.')
        info.setObjectName('muted')
        info.setWordWrap(True)
        layout.addWidget(info)
        self.tabs.addTab(page, 'Notes')
        self.notes, self._notes_key = [], None
        self.notes_timer = QtCore.QTimer(self)
        self.notes_timer.timeout.connect(self.refresh_notes)
        self.notes_timer.start(2000)
        self.refresh_notes(True)

    def note_clock(self):
        """(UTC seconds, time basis) on the same timeline as the recorded data."""
        clock = ((self.last_status or {}).get('last_snapshot') or {}).get('clock') or {}
        same_boot = clock.get('anchor_monotonic_s') is not None and clock.get('system_boot_id') == boot_identity()
        referenced = same_boot and clock.get('last_good_monotonic_s') is not None
        basis = ('recorder_sntp_holdover' if clock.get('quality') == 'holdover' else
                 'recorder_sntp_reference') if referenced else 'host_clock'
        return self.current_timeline_seconds(), basis

    def _notes_run(self):
        return self.run if self.run and (self.run / 'manifest.json').exists() else None

    def mark_seal(self):
        utc, basis = self.note_clock()  # the moment of the click, before any dialog
        run = self._notes_run()
        if not run:
            self.fail('Open or start a recording before marking the seal time')
            return
        local = dt.datetime.fromtimestamp(utc).astimezone()
        question = f'Record the seal time as {local:%Y-%m-%d %H:%M:%S %Z} ({annotations.iso(utc)} UTC)?'
        if annotations.seal(self.notes):
            question += '\n\nThis becomes the new time zero; the earlier seal note is kept.'
        if Q.QMessageBox.question(self, 'Mark seal time', question) != Q.QMessageBox.StandardButton.Yes:
            return
        self._append_note(run, utc, 'seal', 'Seal time marked in GammaKeeper', basis)

    def add_note(self):
        utc, basis = self.note_clock()
        run = self._notes_run()
        if not run:
            self.fail('Open or start a recording before adding notes')
            return
        dialog = NoteDialog(self, utc)
        if dialog.exec() != Q.QDialog.DialogCode.Accepted:
            return
        kind, text, when, entered = dialog.values()
        self._append_note(run, when, kind, text, 'entered_by_user' if entered else basis,
                          event_uncertainty_s=dialog.uncertainty.value() if dialog.uncertainty_known.isChecked() else None)

    def _append_note(self, run, utc, kind, text, basis, event_uncertainty_s=None):
        try:
            clock = ((self.last_status or {}).get('last_snapshot') or {}).get('clock') or {}
            from build_identity import identity
            annotations.add(run, utc, kind, text, basis,
                            dict(clock=clock, event_uncertainty_s=event_uncertainty_s, build=identity()))
        except (OSError, sqlite3.Error, ValueError) as error:
            self.fail(f'Note not saved: {error}')
            return
        self.message(f'{annotations.KIND_LABELS[kind]} noted at {annotations.iso(utc)} UTC')
        self.refresh_notes(True)

    def refresh_notes(self, force=False):
        run = self._notes_run()
        writable = bool(run) and not (run / 'BACKUP_PLAN.json').exists()
        self.seal_button.setEnabled(writable)
        self.note_button.setEnabled(writable)
        path = run / annotations.FILE if run else None
        try:
            key = (str(run), path.stat().st_mtime_ns if path and path.exists() else None)
        except OSError:
            key = (str(run), None)
        if key != self._notes_key or force:
            self._notes_key = key
            try:
                self.notes = annotations.load(run) if run else []
            except (OSError, sqlite3.Error) as error:
                self.notes = []
                self.seal_summary.setText(f'Notes unavailable: {error}')
            self._fill_notes_table()
            self.charts.annotations = [(n['utc'], n['kind'], n['text']) for n in self.notes]
            if self.analysis_rows:
                self.redraw_analysis()
        self._update_seal_summary()

    def _fill_notes_table(self):
        current = annotations.seal(self.notes)
        self.notes_table.setRowCount(len(self.notes))
        for row, note in enumerate(self.notes):
            label = annotations.KIND_LABELS.get(note['kind'], note['kind'])
            if current and note['id'] == current['id']:
                label += ' • current time zero'
            elif note['kind'] == 'seal':
                label += ' • superseded'
            local = dt.datetime.fromtimestamp(note['utc']).astimezone()
            for column, value in enumerate([f'{local:%Y-%m-%d %H:%M:%S %Z}', annotations.iso(note['utc']), label,
                                            note['text'], note['time_basis']]):
                item = Q.QTableWidgetItem(value)
                item.setToolTip(f"Entered {annotations.iso(note['entered_utc'])} UTC • software {note['software_version']}")
                self.notes_table.setItem(row, column, item)

    def _update_seal_summary(self):
        current = annotations.seal(self.notes)
        if not current:
            self.seal_summary.setText('No seal time recorded.' if self._notes_run() else 'Open a recording to add notes.')
            return
        elapsed = self.current_timeline_seconds() - current['utc']
        local = dt.datetime.fromtimestamp(current['utc']).astimezone()
        self.seal_summary.setText(f'Seal time (time zero): {local:%Y-%m-%d %H:%M:%S %Z} • {annotations.iso(current["utc"])} UTC'
                                  f' • {elapsed / 3600:+.2f} h ({elapsed / 86400:+.3f} d) since seal')
