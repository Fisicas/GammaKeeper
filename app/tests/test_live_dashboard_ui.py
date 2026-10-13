"""Plot-first dashboard regression checks using only synthetic in-memory spectra."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['GAMMAKEEPER_TEST_MODE'] = '1'

import datetime as dt
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from PySide6 import QtWidgets as Q
from ui.charts import Charts
from gui import Window
from recorder import make_config
from storage import RunStore
from test_core import snapshot


class LiveDashboardPlots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    @staticmethod
    def temp_root():
        root = Path(__file__).resolve().parents[1] / 'validation' / 'test_temp_live_dashboard'
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_count_and_dose_share_time_but_use_separate_colored_axes(self):
        charts = Charts()
        self.assertIsNot(charts.count, charts.dose)
        self.assertTrue(charts.count.get_shared_x_axes().joined(charts.count, charts.dose))
        self.assertEqual(charts.count.get_ylabel(), 'Count rate (cps)')
        self.assertEqual(charts.dose.get_ylabel(), 'Dose rate (µSv/h)')
        self.assertEqual(charts.count.yaxis.get_label_position(), 'left')
        self.assertEqual(charts.dose.yaxis.get_label_position(), 'right')
        self.assertGreater(charts.dose.get_zorder(), charts.count.get_zorder())
        self.assertFalse(charts.dose.patch.get_visible())
        self.assertEqual(charts.count.yaxis.label.get_color(), '#43d8bf')
        self.assertEqual(charts.dose.yaxis.label.get_color(), '#f5bc66')
        self.assertIsNone(charts.count.get_legend())

        utc = 1791840000.0
        snapshot = dict(spectrum=dict(counts=[10, 10, 10, 10], calibration_keV=[0.0, 1.0, 0.0]))
        interval = dict(counts_delta=[2, 0, 5, 8], device_duration_s=10.0, wall_duration_s=10.0,
                        count_rate_cps=1.5, quality=['normal'])
        saved = dict(spectrum=snapshot['spectrum'], telemetry_bin={
            'coverage_fraction': 1.0, 'dose_rate_uSv_h': 0.05, 'device_count_rate_cps': 1.4})
        charts.show_rows([(utc, 0, interval, saved)], saved, 'device')
        self.assertEqual(len(charts.count.lines), 2)
        self.assertEqual(len(charts.dose.lines), 1)
        self.assertEqual(list(charts.dose.lines[0].get_ydata()), [0.05])
        charts.set_series_visible('estimator', False)
        self.assertFalse(charts.series_artists['estimator'][0].get_visible())

    def test_accumulated_plot_uses_supplied_original_channel_counts(self):
        charts = Charts()
        counts = [12, 0, 31, 57]
        collection = dict(included_intervals=2, requested_start_utc=None,
                          selection_rule='Complete recorded intervals only')
        row = (1791840000.0, [0], dict(counts_delta=counts),
               dict(spectrum=dict(counts=counts, calibration_keV=[0.0, 1.0, 0.0]),
                    collection=collection))
        charts.set_accumulated(row)
        plotted = charts.accum.lines[0]
        self.assertEqual(list(plotted.get_ydata()), counts[:-1])
        self.assertIn('2 complete intervals', charts.accum.get_title(loc='left'))

    def test_recording_shell_hides_setup_and_diagnostics_behind_controls(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as scratch:
            run = Path(scratch) / 'empty-read-only-run'
            run.mkdir()
            window = Window(run, read_only=True)
            self.assertTrue(window.setup_scroll.isHidden())
            self.assertTrue(window.tabs.tabBar().isHidden())
            self.assertTrue(window.details_container.isHidden())
            self.assertEqual(window.tabs.currentIndex(), 0)
            window.settings_toggle.setChecked(True)
            self.assertFalse(window.setup_scroll.isHidden())
            window.close()
            self.app.processEvents()

    def test_live_accumulated_spectrum_loads_from_run_in_background(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as scratch:
            run = Path(scratch) / 'synthetic-run'
            store = RunStore(run, make_config('TEST', 10, 0, 'demo', 'dashboard accumulation'))
            store.append_snapshot(snapshot(0, [100, 200, 300]))
            store.append_snapshot(snapshot(10, [103, 207, 311]))
            store.set_state('ui_status', dict(mode='demo', serial='TEST', state='stopped', pid=0,
                                               boot_id='test', utc=dt.datetime.now(dt.timezone.utc).isoformat()))
            store.set_state('recorder', dict(status='stopped', pid=0, boot_id='test'))
            store.close()

            window = Window(run, read_only=True)
            window.timer.stop()
            try:
                deadline = time.monotonic() + 15
                while window.dashboard_accumulation_busy or window.dashboard_accumulation_row is None:
                    self.app.processEvents()
                    if time.monotonic() >= deadline:
                        self.fail(window.dashboard_accumulation_error or 'Dashboard accumulation did not finish')
                    time.sleep(.01)
                self.assertEqual(window.dashboard_accumulation_row[2]['counts_delta'], [3, 7, 11])
                self.assertIn('1 complete intervals', window.charts.accum.get_title(loc='left'))
                deadline = time.monotonic() + 15
                while window.preview_busy:
                    self.app.processEvents()
                    if time.monotonic() >= deadline:
                        self.fail('Dashboard preview did not finish')
                    time.sleep(.01)
                window.refresh()
                self.assertIn('Display: individual recorded intervals', window.plot_cadence.text())
            finally:
                window._closing = True
                window.dashboard_accumulation_cancel.set()
                window.dashboard_accumulation_pool.waitForDone(10000)
                window.preview_pool.waitForDone(10000)
                window.close()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
