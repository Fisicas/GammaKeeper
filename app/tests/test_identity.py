import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen');os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import json
import struct
import tempfile
import unittest
from PySide6 import QtCore, QtSvg, QtWidgets as Q
from ui.identity import icon_svg, small_icon_svg, splash_svg
from ui.splash import SplashScreen, app_icon, run_identity

RESOURCES = Path(__file__).resolve().parents[1]/'ui'/'resources'


def valid(svg):
    return QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode('utf-8'))).isValid()


class IdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def test_artwork_renders(self):
        self.assertTrue(valid(icon_svg()))
        self.assertTrue(valid(small_icon_svg()))
        steps = [('done', 'Loading', 0.1), ('active', 'Building', None), ('todo', 'Opening', None)]
        self.assertTrue(valid(splash_svg('0.5.1', steps, 'label <with> & markup', 'run_id')))

    def test_run_label_is_escaped(self):
        svg = splash_svg('0.5.1', (), 'a<b & c>', 'x')
        self.assertIn('a&lt;b &amp; c&gt;', svg)
        self.assertNotIn('a<b', svg)

    def test_icon_has_small_and_large_sizes(self):
        sizes = {size.width() for size in app_icon().availableSizes()}
        self.assertTrue({16, 24, 32, 48, 256} <= sizes)

    def test_splash_steps_and_run_label(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)/'20261014_091530_RC-TEST'
            run.mkdir()
            (run/'manifest.json').write_text(json.dumps({'label': 'Rn-222 canister 3'}), encoding='utf-8')
            self.assertEqual(run_identity(run), ('Rn-222 canister 3', run.name))
            splash = SplashScreen('0.5.1', ['one', 'two'], run)
            splash.begin(0); splash.begin(1); splash.finish(delay_ms=0)
            self.assertEqual([s[0] for s in splash.steps], ['done', 'done'])
            self.assertTrue(all(s[2] is not None and s[2] >= 0 for s in splash.steps))
            self.assertIn('Rn-222 canister 3', splash.svg())
            splash.close()
        self.assertEqual(run_identity(None), (None, None))

    def test_committed_ico_has_all_sizes(self):
        data = (RESOURCES/'gammakeeper.ico').read_bytes()
        reserved, kind, count = struct.unpack('<HHH', data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 8))
        sizes = [data[6 + 16*i] or 256 for i in range(count)]
        self.assertEqual(sizes, [16, 20, 24, 32, 40, 48, 64, 256])
        for i in range(count):
            length, offset = struct.unpack('<II', data[6 + 16*i + 8:6 + 16*i + 16])
            self.assertEqual(data[offset:offset + 8], b'\x89PNG\r\n\x1a\n')


if __name__ == '__main__':
    unittest.main()
