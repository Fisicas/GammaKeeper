import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['GAMMAKEEPER_TEST_MODE'] = '1'
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PySide6 import QtWidgets as Q
from time_units import parse_time
from time_input import TimeInput
from time_resolved import export_series, group_indices, read_rcspg, load_segments
from run_policy import validate_schedule
from recorder import make_config
from storage import RunStore
from test_core import snapshot
from gui import Window


class TimeExpressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def test_equivalent_and_compound_units(self):
        for expression in ('60 minutes', '1 hour', '3600 s', '1:00:00', '1 h'):
            self.assertEqual(parse_time(expression), 3600)
        self.assertEqual(parse_time('1h30min'), 5400)
        self.assertEqual(parse_time('1 week 2 days 3h 4min 5.5s'), 788645.5)
        self.assertEqual(parse_time('1e-1 min'), 6)
        self.assertEqual(parse_time('500ms'), .5)
        self.assertEqual(parse_time('1', 'days'), 86400)
        self.assertEqual(parse_time('continuous', continuous=True), 0)

    def test_reject_ambiguous_invalid_and_nonfinite(self):
        for expression in ('-1 h', 'nan', 'inf', '1e999 s', '1:30', '1:60:00', '1.5:00:00', '2 moons', '1h junk', '1 + 2 s', ''):
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                parse_time(expression)
        with self.assertRaises(ValueError):
            validate_schedule(.5, 3600)
        validate_schedule(parse_time('1 hour'), parse_time('1 week'))

    def test_widget_defaults_explicit_units_and_invalid_text(self):
        widget = TimeInput('days', continuous=True)
        widget.setRange(0, 365)
        widget.edit.setText('60 minutes')
        self.assertEqual(widget.seconds(), 3600)
        self.assertAlmostEqual(widget.value(), 1/24)
        self.assertEqual(widget.units.currentText(), 'min')
        widget.units.setCurrentText('days')
        self.assertAlmostEqual(widget.seconds(), 3600, places=9)
        widget.edit.setText('2')
        self.assertEqual(widget.seconds(), 172800)
        widget.units.setCurrentText('h')
        self.assertEqual(widget.seconds(), 7200)
        widget.edit.setText('not a duration')
        with self.assertRaises(ValueError):
            widget.value()
        widget.deleteLater()


class SpectralSeries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / 'validation' / 'time_export_temp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temp.name)
        self.zero = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()

    def tearDown(self):
        self.temp.cleanup()

    def run_data(self, gap=False, calibration=False):
        run = self.root / 'run'
        store = RunStore(run, make_config('TEST', 10, 0, 'demo', 'time series'))
        for i in range(8):
            value = snapshot(i*10, [100+i, 200+2*i, 300+3*i])
            if gap and i == 4:
                value['had_disconnect'] = True
            if calibration and i >= 4:
                value['spectrum']['calibration_keV'][0] += 1
            store.append_snapshot(value)
        store.set_state('ui_status', dict(state='stopped', pid=0, utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        store.set_state('recorder', dict(status='stopped', pid=0))
        store.close()
        return run

    def output(self, run, **kwargs):
        target, report = export_series(run, self.root/'export', **kwargs)
        arrays = np.load(target/(report['segments'][0]['stem']+'.npz'))
        return target, report, arrays

    def test_native_counts_endpoint_difference_and_no_source_writes(self):
        run = self.run_data()
        files = [run/'manifest.json', run/'run.sqlite', *run.glob('spectra/**/*.json')]
        before = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
        target, report, arrays = self.output(run)
        self.assertEqual(arrays['counts'].shape, (7, 3))
        np.testing.assert_array_equal(arrays['counts'].sum(axis=0), [7, 14, 21])
        np.testing.assert_allclose(arrays['time_s'], [5, 15, 25, 35, 45, 55, 65])
        np.testing.assert_allclose(arrays['rate_cps'][0], [.1, .2, .3])
        value, restored = read_rcspg(target / report['segments'][0]['rcspg'])
        np.testing.assert_array_equal(restored, arrays['counts'])
        self.assertEqual(value['startTimeTimestamp'], self.zero*1000)
        self.assertEqual(value['spectrums'][0]['timestamp'], (self.zero+10)*1000)
        self.assertEqual(before, {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in files})
        arrays.close()

    def test_width_conserves_counts_actual_exposure_and_short_tail(self):
        target, report, arrays = self.output(self.run_data(), mode='width', width_s=30)
        np.testing.assert_array_equal(arrays['counts'], [[3, 6, 9], [3, 6, 9], [1, 2, 3]])
        np.testing.assert_array_equal(arrays['device_exposure_s'], [30, 30, 10])
        rows = [json.loads(line) for line in (target/(report['segments'][0]['stem']+'_bins.jsonl')).read_text().splitlines()]
        self.assertTrue(rows[-1]['final_short_group'])
        arrays.close()

    def test_number_balances_complete_intervals_without_interpolation(self):
        _, _, arrays = self.output(self.run_data(), mode='number', number=3)
        np.testing.assert_array_equal(arrays['last_seq']-arrays['first_seq']+1, [2, 2, 3])
        np.testing.assert_array_equal(arrays['counts'].sum(axis=0), [7, 14, 21])
        arrays.close()

    def test_sliding_overlap_explicit_and_rcspg_omitted(self):
        target, report, arrays = self.output(self.run_data(), mode='sliding', width_s=30, step_s=10)
        self.assertEqual(arrays['counts'].shape, (5, 3))
        self.assertTrue(report['overlapping'])
        self.assertTrue(report['warnings'])
        self.assertIsNone(report['segments'][0]['rcspg'])
        self.assertFalse(list(target.glob('*.rcspg')))
        self.assertEqual(report['segments'][0]['unique_source_counts'], 42)
        self.assertEqual(report['segments'][0]['output_sum_counts'], 90)
        arrays.close()

    def test_strict_resolution_more_bins_and_existing_output_rejected(self):
        run = self.run_data()
        for kwargs in (dict(mode='width', width_s=5), dict(mode='width', width_s=15), dict(mode='number', number=8),
                       dict(mode='sliding', width_s=20, step_s=30), dict(mode='sliding', width_s=100, step_s=10)):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                export_series(run, self.root/'invalid', **kwargs)
            self.assertFalse((self.root/'invalid').exists())
        self.output(run)[2].close()
        with self.assertRaises(FileExistsError):
            export_series(run, self.root/'export')

    def test_complete_range_and_custom_zero(self):
        _, report, arrays = self.output(self.run_data(), start=self.zero+5, end=self.zero+35, time_zero=self.zero+20)
        np.testing.assert_array_equal(arrays['first_seq'], [2, 3])
        np.testing.assert_allclose(arrays['time_s'], [-5, 5])
        self.assertEqual(report['excluded_intervals'], 2)
        arrays.close()

    def test_gap_aggregate_retained_separately_and_no_bins_cross_it(self):
        target, report, arrays = self.output(self.run_data(gap=True), mode='width', width_s=30)
        self.assertEqual(len(report['segments']), 2)
        excluded = [json.loads(line) for line in (target/'excluded_intervals.jsonl').read_text().splitlines()]
        gap = [r for r in excluded if r['seq'] == 4][0]
        self.assertEqual(gap['counts_delta'], [1, 2, 3])
        self.assertIn('gap_recovered_aggregate', gap['reason'])
        arrays.close()

    def test_calibrations_split_epochs_and_number_mode_rejects_crossing(self):
        run = self.run_data(calibration=True)
        _, report, arrays = self.output(run)
        self.assertEqual(len(report['segments']), 2)
        self.assertNotEqual(report['segments'][0]['calibration_keV'], report['segments'][1]['calibration_keV'])
        with self.assertRaisesRegex(ValueError, 'contiguous'):
            export_series(run, self.root/'number', mode='number', number=2)
        arrays.close()

    def test_xlsx_orientation_energy_conversion_and_roi_preserves_full_counts(self):
        from openpyxl import load_workbook
        target, report, arrays = self.output(self.run_data(), xlsx=True, energy_min=5, energy_max=7)
        book = load_workbook(target/(report['segments'][0]['stem']+'_UFit_rate_cps.xlsx'), read_only=True)
        values = list(book.active.values)
        self.assertEqual(values[0], ('Time (s)', '5000 eV'))
        self.assertEqual(values[1], (5, .1))
        self.assertEqual(len(values), 8)
        self.assertEqual(arrays['counts'].shape, (7, 3))
        book.close()
        arrays.close()

    def test_hash_corruption_fails_without_complete_output(self):
        run = self.run_data()
        db = __import__('export_run').readonly(run/'run.sqlite')
        name = db.execute('SELECT file FROM snapshots WHERE seq=2').fetchone()[0]
        db.close()
        with (run/name).open('ab') as file:
            file.write(b' ')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            export_series(run, self.root/'invalid')
        self.assertFalse((self.root/'invalid').exists())

    def test_gui_start_uses_entered_hours_and_minutes_and_export_arguments(self):
        run = self.run_data()
        window = Window(run)
        window.timer.stop()
        try:
            window.export_mode.setCurrentIndex(1)
            window.export_width.edit.setText('1 minute')
            window.export_xlsx.setChecked(True)
            window.export_custom_zero.setChecked(True)
            with patch.object(Q.QFileDialog, 'getExistingDirectory', return_value=str(self.root)), patch.object(window, 'run_job') as job:
                window.export_series_button.click()
            args = job.call_args.args
            self.assertEqual(args[0], 'time_resolved.py')
            self.assertIn('60.0', args)
            self.assertIn('--xlsx', args)
            self.assertIn('--time-zero-utc', args)
            window.prepare_new()
            window.days.edit.setText('1 hour')
            window.interval.edit.setText('2 minutes')
            with patch.object(window, 'launch') as launch:
                window.start_new()
            args = launch.call_args.args[0]
            self.assertEqual(args[args.index('--seconds')+1], '3600.0')
            self.assertEqual(args[args.index('--interval')+1], '120.0')
            window.days.edit.setText('invalid')
            with patch.object(window, 'fail') as fail, patch.object(window, 'launch') as launch:
                window.start_new()
            self.assertTrue(fail.called)
            self.assertFalse(launch.called)
        finally:
            window.close()
            window.preview_pool.waitForDone(10000)
            window.accumulation_pool.waitForDone(10000)
            window.deleteLater()
            self.app.processEvents()
