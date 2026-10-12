"""Write GammaKeeper identity files from app/ui/identity.py (the single source of the artwork).

Outputs:
  app/ui/resources/gammakeeper.svg, gammakeeper-small.svg   editable masters
  app/ui/resources/gammakeeper.ico                          16-256 px; 16-24 px use the small glyph
  docs/images/icon-256.png, splash.png, social-preview.png  README hero and GitHub social preview
  docs/images/screenshot-*.png                              with --run: real window crops of a saved run

Usage:
  python -X utf8 tools/make_identity.py [--run SAVED_RUN_COPY]
Use a copy of a run for --run: the window writes its display cache in app/cache, never in the run.
"""
import argparse
import io
import os
import struct
import sys
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['GAMMAKEEPER_TEST_MODE'] = '1'  # do not read or write GUI preferences
REPO = Path(__file__).resolve().parents[1]
APP = REPO / 'app'
sys.path.insert(0, str(APP))

import bootstrap  # noqa: E402,F401
from PySide6 import QtCore, QtGui, QtWidgets as Q  # noqa: E402
from analysis import VERSION  # noqa: E402
from ui.identity import icon_svg, small_icon_svg, splash_svg  # noqa: E402
from ui.splash import SMALL_SIZES, render_svg  # noqa: E402

RESOURCES = APP / 'ui' / 'resources'
IMAGES = REPO / 'docs' / 'images'
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 256)


def png_bytes(image):
    buffer = QtCore.QBuffer()
    buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    return bytes(buffer.data())


def write_ico(path):
    """Multi-size ICO with PNG-compressed entries (Windows Vista and later)."""
    images = [png_bytes(render_svg(small_icon_svg() if s in SMALL_SIZES else icon_svg(), s)) for s in ICO_SIZES]
    out = io.BytesIO()
    out.write(struct.pack('<HHH', 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for size, data in zip(ICO_SIZES, images):
        dim = 0 if size >= 256 else size
        out.write(struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(data), offset))
        offset += len(data)
    for data in images:
        out.write(data)
    path.write_bytes(out.getvalue())


def write_images():
    IMAGES.mkdir(parents=True, exist_ok=True)
    render_svg(icon_svg(), 256).save(str(IMAGES / 'icon-256.png'))
    steps = [('done', 'Loading fonts and style', 0.1), ('done', 'Building window and reading saved run', 1.4),
             ('active', 'Opening main window', None)]
    hero = splash_svg(VERSION, steps)
    render_svg(hero, 1440, 810).save(str(IMAGES / 'splash.png'))
    social = QtGui.QImage(1280, 640, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    social.fill(QtGui.QColor('#0b131b'))
    painter = QtGui.QPainter(social)
    painter.drawImage(71, 0, render_svg(hero, 1138, 640))
    painter.end()
    social.save(str(IMAGES / 'social-preview.png'))


def write_screenshots(run):
    """Crop the plot area of the Time series and Spectrum tabs (no file paths or settings panel)."""
    from ui.main_window import Window
    window = Window(str(run))
    window.resize(1500, 980)
    window.show()
    app = Q.QApplication.instance()

    def settle(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.05)

    settle(2)
    deadline = time.monotonic() + 30
    while (window.preview_busy or window.accumulation_busy) and time.monotonic() < deadline:
        window.refresh()
        settle(0.3)
    for index, name in ((0, 'screenshot-time-series.png'), (1, 'screenshot-spectrum.png')):
        window.tabs.setCurrentIndex(index)
        settle(1.5)
        while window.accumulation_busy and time.monotonic() < deadline:
            settle(0.3)
        window.charts.draw()
        window.spectrum_canvas.draw()
        image = window.grab().toImage()
        image.copy(QtCore.QRect(342, 60, 1158, 732)).save(str(IMAGES / name))
    window._closing = True
    window.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', type=Path, help='copy of a saved run for the README screenshots')
    args = parser.parse_args()
    app = Q.QApplication(sys.argv[:1])
    for name in ['segoeui.ttf', 'segoeuib.ttf', 'seguisb.ttf']:
        font = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts' / name
        if font.exists():
            QtGui.QFontDatabase.addApplicationFontFromData(QtCore.QByteArray(font.read_bytes()))
    app.setFont(QtGui.QFont('Segoe UI', 10))
    app.setStyle('Fusion')
    RESOURCES.mkdir(parents=True, exist_ok=True)
    (RESOURCES / 'gammakeeper.svg').write_text(icon_svg(), encoding='utf-8', newline='\n')
    (RESOURCES / 'gammakeeper-small.svg').write_text(small_icon_svg(), encoding='utf-8', newline='\n')
    write_ico(RESOURCES / 'gammakeeper.ico')
    write_images()
    if args.run:
        write_screenshots(args.run)
    print('identity files written:', RESOURCES, IMAGES)
    return 0


if __name__ == '__main__':
    sys.exit(main())
