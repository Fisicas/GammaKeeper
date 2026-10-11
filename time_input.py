"""Text time entry with explicit units and strict validation at submission."""
from PySide6 import QtWidgets as Q, QtCore
from time_units import parse_time, display_time, TERM, UNITS, NUMBER
import re


class TimeInput(Q.QWidget):
    valueChanged = QtCore.Signal(float)

    def __init__(self, base_unit='s', continuous=False):
        super().__init__()
        self.base_unit = base_unit
        self.scale = 86400 if base_unit == 'days' else 1
        self.continuous = continuous
        self.minimum, self.maximum = 0., float('inf')
        self.edit = Q.QLineEdit()
        self.units = Q.QComboBox()
        for name in ('s', 'min', 'h', 'days'):
            self.units.addItem(name)
        self.units.setCurrentText(base_unit)
        self.units.setToolTip('Default unit for a number without units; explicit units always take precedence.')
        layout = Q.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.units)
        self.edit.setPlaceholderText('e.g. 60 minutes, 1 hour, 1h 30min')
        self.edit.setToolTip('Seconds, minutes, hours, days or weeks; decimals and compound times accepted. HH:MM:SS is also accepted.')
        self.edit.textChanged.connect(self.show_validation)
        self.units.currentTextChanged.connect(self.unit_changed)
        self.edit.editingFinished.connect(self.emit_value)

    def lineEdit(self):
        return self.edit

    def seconds(self):
        result = parse_time(self.edit.text(), self.units.currentText(), self.continuous)
        if not self.minimum * self.scale <= result <= self.maximum * self.scale:
            raise ValueError(f'Time must be within {self.minimum * self.scale:g}–{self.maximum * self.scale:g} seconds')
        return result

    def value(self):
        return self.seconds() / self.scale

    def setValue(self, value):
        self.edit.setText(display_time(float(value) * self.scale))

    def setRange(self, minimum, maximum):
        self.minimum, self.maximum = float(minimum), float(maximum)

    def setMaximum(self, maximum):
        self.maximum = float(maximum)

    def setDecimals(self, unused):
        pass

    def setSuffix(self, unused):
        pass

    def show_validation(self, *unused):
        # Explicit units in typed text also update the selector, so "1 hour"
        # does not appear beside a contradictory "days" selector.
        terms = list(TERM.finditer(self.edit.text().lower()))
        if terms:
            scale = UNITS.get(terms[-1][2])
            unit = {1: 's', 60: 'min', 3600: 'h', 86400: 'days'}.get(scale)
            if unit and unit != self.units.currentText():
                blocked = self.units.blockSignals(True)
                self.units.setCurrentText(unit)
                self.units.blockSignals(blocked)
        try:
            seconds = self.seconds()
            self.edit.setStyleSheet('')
            self.setToolTip('Continuous recording' if seconds == 0 and self.continuous else f'{seconds:.12g} seconds')
        except ValueError as error:
            self.edit.setStyleSheet('border: 1px solid #ec9b72;')
            self.setToolTip(str(error))

    def unit_changed(self, unit):
        text = self.edit.text().strip()
        # Changing the unit of an explicit duration converts the representation.
        # For a bare number, the selector instead supplies its unit.
        if text and not re.fullmatch(NUMBER, text):
            try:
                seconds = parse_time(text, continuous=self.continuous)
                if text.lower() not in ('continuous', 'unlimited'):
                    self.edit.setText(f'{seconds / UNITS[unit]:.17g} {unit}')
            except ValueError:
                pass
        self.show_validation()

    def emit_value(self):
        try:
            self.valueChanged.emit(self.value())
        except ValueError:
            pass
