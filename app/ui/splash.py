"""Startup splash, window icon and taskbar identity (plan sections 3.1-3.2).

The splash only reads the run manifest for its label; it never touches the device or
the recorder, and it closes as soon as the main window is shown.
"""
import ctypes
import json
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtSvg, QtWidgets as Q

from ui.identity import APP_ID, icon_svg, small_icon_svg, splash_svg

SMALL_SIZES = (16, 20, 24)
LARGE_SIZES = (32, 40, 48, 64, 128, 256)


def render_svg(text, width, height=None):
    height = height or width
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.SmoothPixmapTransform)
    QtSvg.QSvgRenderer(QtCore.QByteArray(text.encode('utf-8'))).render(painter, QtCore.QRectF(0, 0, width, height))
    painter.end()
    return image


def app_icon():
    """Window/taskbar icon: plain glyph at 16-24 px, keeper ring from 32 px."""
    icon = QtGui.QIcon()
    for size in SMALL_SIZES:
        icon.addPixmap(QtGui.QPixmap.fromImage(render_svg(small_icon_svg(), size)))
    for size in LARGE_SIZES:
        icon.addPixmap(QtGui.QPixmap.fromImage(render_svg(icon_svg(), size)))
    return icon


def set_app_user_model_id():
    """Group this process's windows under GammaKeeper instead of Python (per process; no registry change)."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(APP_ID))
        return True
    except (AttributeError, OSError):
        return False


def run_identity(run):
    """(label, folder name) of a saved run for the splash, or (None, None)."""
    try:
        path = Path(run)
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
        return manifest.get('label') or None, path.name
    except (TypeError, OSError, ValueError, AttributeError):
        return None, None


class SplashScreen(Q.QWidget):
    def __init__(self, version, steps, run=None):
        flags = (QtCore.Qt.WindowType.SplashScreen | QtCore.Qt.WindowType.FramelessWindowHint
                 | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        super().__init__(None, flags)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(720, 405)
        self.setWindowTitle('GammaKeeper')
        self.version = version
        self.label, self.run_id = run_identity(run)
        self.steps = [['todo', text, None] for text in steps]
        self._current = None
        self._started = None
        screen = Q.QApplication.primaryScreen()
        if screen:
            self.move(screen.availableGeometry().center() - self.rect().center())

    def begin(self, index):
        self.done()
        self._current, self._started = index, time.monotonic()
        self.steps[index][0] = 'active'
        self._refresh()

    def done(self):
        if self._current is not None:
            step = self.steps[self._current]
            step[0], step[2] = 'done', time.monotonic() - self._started
            self._current = None
            self._refresh()

    def finish(self, delay_ms=400):
        self.done()
        QtCore.QTimer.singleShot(delay_ms, self.close)

    def svg(self):
        return splash_svg(self.version, [tuple(step) for step in self.steps], self.label, self.run_id)

    def _refresh(self):
        self.repaint()
        Q.QApplication.processEvents()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHints(QtGui.QPainter.RenderHint.Antialiasing | QtGui.QPainter.RenderHint.TextAntialiasing)
        QtSvg.QSvgRenderer(QtCore.QByteArray(self.svg().encode('utf-8'))).render(painter, QtCore.QRectF(self.rect()))
        painter.end()
