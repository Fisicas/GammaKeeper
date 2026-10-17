import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen');os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import unittest
from PySide6 import QtWidgets as Q
from gui import Window
from ui.charts import time_format
from ui.timeseries_tab import DEFAULT_WINDOW
import display_controls


class TimeWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def test_time_format_follows_span(self):
        self.assertEqual(time_format(5/1440), '%H:%M:%S')
        self.assertEqual(time_format(6/24), '%H:%M')
        self.assertEqual(time_format(3), '%m-%d %H:%M')
        self.assertEqual(time_format(.8, show_dates=True), '%m-%d %H:%M')

    def test_default_window_and_colormap(self):
        window = Window()
        try:
            self.assertEqual(window.view_mode.currentData(), DEFAULT_WINDOW)
            self.assertEqual(window.spectrogram_colormap.currentData(), display_controls.DEFAULT_COLORMAP)
            self.assertEqual(window.charts.colormap, 'cividis')
            self.assertEqual(window.color_percentile.value(),100)
            windows = [window.view_mode.itemData(i) for i in range(window.view_mode.count())]
            self.assertEqual(windows, [300, 1800, 3600, 21600, 86400, 'recent', 'full', 'window'])
        finally:
            window._closing = True; window.close()

    def test_view_preferences_round_trip(self):
        window = Window()
        try:
            window.restore_view(dict(window=300, colormap='cividis'))
            self.assertEqual(window.view_preferences(), dict(window=300, colormap='cividis'))
            self.assertEqual(window.charts.colormap, 'cividis')
            window.restore_view(dict(window='nonsense', colormap='not-a-map'))
            self.assertEqual(window.view_preferences(), dict(window=300, colormap='cividis'))
        finally:
            window._closing = True; window.close()


if __name__ == '__main__':
    unittest.main()
