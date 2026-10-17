"""Two-handle display range control; values retain the units of its caller."""
from PySide6 import QtCore, QtGui, QtWidgets as Q


class RangeSlider(Q.QWidget):
    rangeChanged = QtCore.Signal(float, float)
    rangeCommitted = QtCore.Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimum = 0.0
        self._maximum = 1.0
        self._low = 0.0
        self._high = 1.0
        self._active = None
        self.setMinimumHeight(32)
        self.setMinimumWidth(140)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setToolTip('Drag either handle to choose the start or end; type an exact value in the boxes.')

    def domain(self):
        return self._minimum, self._maximum

    def values(self):
        return self._low, self._high

    def set_domain(self, minimum, maximum, preserve=False):
        minimum, maximum = float(minimum), float(maximum)
        if maximum <= minimum:
            raise ValueError('Range slider domain must increase')
        old = self.values()
        self._minimum, self._maximum = minimum, maximum
        self.set_values(*(old if preserve else (minimum, maximum)), emit=False)

    def set_values(self, low, high, emit=True):
        low = min(self._maximum, max(self._minimum, float(low)))
        high = min(self._maximum, max(low, float(high)))
        changed = (low, high) != (self._low, self._high)
        self._low, self._high = low, high
        self.update()
        if changed and emit:
            self.rangeChanged.emit(low, high)

    def _x(self, value):
        width = max(1, self.width() - 24)
        return 12 + width * (value - self._minimum) / (self._maximum - self._minimum)

    def _value(self, x):
        fraction = min(1.0, max(0.0, (x - 12) / max(1, self.width() - 24)))
        return self._minimum + fraction * (self._maximum - self._minimum)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        y = self.height() / 2
        painter.setPen(QtGui.QPen(QtGui.QColor('#314357'), 6, QtCore.Qt.PenStyle.SolidLine,
                                  QtCore.Qt.PenCapStyle.RoundCap))
        painter.drawLine(QtCore.QPointF(12, y), QtCore.QPointF(self.width() - 12, y))
        painter.setPen(QtGui.QPen(QtGui.QColor('#43d8bf'), 6, QtCore.Qt.PenStyle.SolidLine,
                                  QtCore.Qt.PenCapStyle.RoundCap))
        painter.drawLine(QtCore.QPointF(self._x(self._low), y), QtCore.QPointF(self._x(self._high), y))
        for value in (self._low, self._high):
            painter.setBrush(QtGui.QColor('#f5bc66'))
            painter.setPen(QtGui.QPen(QtGui.QColor('#1b2938'), 2))
            painter.drawEllipse(QtCore.QPointF(self._x(value), y), 7, 7)

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        self._active = 'low' if abs(x - self._x(self._low)) <= abs(x - self._x(self._high)) else 'high'
        self.setFocus()
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if self._active is None:
            return
        value = self._value(event.position().x())
        if self._active == 'low':
            self.set_values(min(value, self._high), self._high)
        else:
            self.set_values(self._low, max(value, self._low))

    def mouseReleaseEvent(self, event):
        if self._active is not None:
            self.mouseMoveEvent(event)
            self._active = None
            self.rangeCommitted.emit(*self.values())

    def keyPressEvent(self, event):
        if event.key() not in (QtCore.Qt.Key.Key_Left, QtCore.Qt.Key.Key_Right):
            return super().keyPressEvent(event)
        step = (self._maximum - self._minimum) / 100
        delta = step if event.key() == QtCore.Qt.Key.Key_Right else -step
        if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
            self.set_values(self._low, self._high + delta)
        else:
            self.set_values(self._low + delta, self._high)
        self.rangeCommitted.emit(*self.values())
