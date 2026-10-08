"""Regression checks for saved-run identity, schedule and GUI reopening."""
import os
os.environ['RADIALOG_TEST_MODE']='1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
import datetime as dt
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch

from PySide6 import QtWidgets as Q
from gui import Window
from run_settings import run_summary


class SavedRunSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / 'validation' / 'gui_settings_temp'
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_root)
        self.root = Path(self.temp.name)
        self.windows = []
        self.saved = dict(label='overnight test', serial='RC-103-006489', mode='device',
                          interval_s=5., duration_s=51840.,
                          created_utc='2026-10-07T20:57:26.945286+00:00',
                          planned_end_utc='2026-10-08T11:21:26.945286+00:00')
        created=dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=10)
        self.saved.update(created_utc=created.isoformat(),planned_end_utc=(created+dt.timedelta(seconds=51840)).isoformat())

    def tearDown(self):
        for window in self.windows:
            window.timer.stop()
            window.close()
            window.deleteLater()
        self.app.processEvents()
        assert self.root.resolve().parent == self.test_root.resolve()
        self.temp.cleanup()

    def make_run(self, name, saved):
        run = self.root / name
        run.mkdir()
        (run / 'manifest.json').write_text(json.dumps(saved), encoding='utf-8')
        return run

    def window(self, run):
        window = Window(run)
        window.timer.stop()
        self.windows.append(window)
        return window

    def test_reopening_restores_real_run_and_preserves_manifest(self):
        run = self.make_run('overnight', self.saved)
        before = hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest()
        window = self.window(run)
        self.assertEqual(window.label.text(), 'overnight test')
        self.assertEqual(window.interval.value(), 5.)
        self.assertAlmostEqual(window.days.value(), .6)
        self.assertEqual(window.folder.text(), str(run.parent))
        self.assertFalse(window.label.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        self.assertIn('Duration: 14 h 24 min', window.run_summary.text())
        self.assertIn('Planned end:', window.run_summary.text())
        self.assertEqual(before, hashlib.sha256((run / 'manifest.json').read_bytes()).hexdigest())

    def test_resume_retains_original_end_instead_of_restarting_duration(self):
        run = self.make_run('resume', self.saved)
        window = self.window(run)
        with patch.object(window, 'launch') as launch:
            window.resume_run()
        launch.assert_called_once_with(['--run', str(run), '--resume'])
        self.assertEqual(window.run_settings['planned_end_utc'], self.saved['planned_end_utc'])
        end=dt.datetime.fromisoformat(self.saved['planned_end_utc'])
        summary = run_summary(self.saved,end-dt.timedelta(minutes=21))
        self.assertIn('Time to planned end: 21 min', summary)
        self.assertIn('Scheduled end reached', run_summary(self.saved,end+dt.timedelta(seconds=1)))

    def test_switching_to_continuous_run_then_new_setup(self):
        window = self.window(self.make_run('first', self.saved))
        continuous = dict(self.saved, label='continuous', interval_s=2.5,
                          duration_s=0., planned_end_utc=None, mode='demo', serial='SIMULATED')
        window.run = self.make_run('second', continuous)
        window.refresh()
        self.assertEqual(window.label.text(), 'continuous')
        self.assertEqual(window.interval.value(), 2.5)
        self.assertEqual(window.days.value(), 0.)
        self.assertTrue(window.demo.isChecked())
        self.assertIn('None (continuous)', window.run_summary.text())
        window.prepare_new()
        self.assertIsNone(window.run)
        self.assertTrue(window.label.isEnabled())
        self.assertTrue(window.start_button.isEnabled())
        self.assertFalse(window.resume_button.isEnabled())
        self.assertEqual(window.interval.value(), 2.5)

    def test_invalid_manifest_does_not_present_defaults_as_saved_values(self):
        saved = dict(self.saved)
        saved.pop('duration_s')
        window = self.window(self.make_run('invalid', saved))
        self.assertIn('Saved run settings unavailable', window.run_summary.text())
        self.assertFalse(window.resume_button.isEnabled())
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(window.interval.isEnabled())
        self.assertTrue(window.setup_button.isEnabled())

    def test_expired_run_is_disabled_and_never_launched(self):
        saved=dict(self.saved,planned_end_utc='2026-01-01T00:00:00+00:00')
        run=self.make_run('expired',saved)
        window=self.window(run)
        with patch.object(window,'fail') as fail,patch.object(window,'launch') as launch:
            window.resume_run()
        launch.assert_not_called();fail.assert_called_once()


if __name__ == '__main__':
    unittest.main()
