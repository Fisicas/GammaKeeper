"""Exercise the per-run recovery choice without launching a supervisor or touching Task Scheduler."""
import datetime as dt
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ['GAMMAKEEPER_TEST_MODE'] = '1'
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6 import QtWidgets as Q
from gui import Window


class RestartRecoveryControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def window(self, run=None):
        window = Window(run)
        window.timer.stop()
        self.addCleanup(window.close)
        return window

    def test_new_run_enables_recovery_by_default_and_allows_opt_out(self):
        with tempfile.TemporaryDirectory() as scratch:
            for requested in (False, True):
                with self.subTest(requested=requested):
                    window = self.window()
                    self.assertTrue(window.restart_recovery.isChecked())
                    window.restart_recovery.setChecked(requested)
                    window.demo.setChecked(True)
                    window.folder.setText(scratch)
                    with patch.object(window, 'launch') as launch, patch.object(window, 'fail') as fail:
                        window.start_new()
                    fail.assert_not_called()
                    launch.assert_called_once()
                    args = launch.call_args.args[0]
                    self.assertEqual('--recover-after-restart' in args, requested)
                    self.assertIn('--demo', args)
                    self.assertNotIn('--no-recover-after-restart', args)
                    window.prepare_new()
                    self.assertTrue(window.restart_recovery.isChecked())

    def test_manual_resume_can_explicitly_enable_or_disable_recovery(self):
        with tempfile.TemporaryDirectory() as scratch:
            run = Path(scratch)
            created = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
            (run / 'manifest.json').write_text(json.dumps(dict(
                label='recovery test', serial='SIMULATED', mode='demo', interval_s=10., duration_s=3600.,
                created_utc=created.isoformat(), planned_end_utc=(created + dt.timedelta(hours=1)).isoformat(),
            )), encoding='utf-8')
            for saved, requested, expected in (
                    (False, True, '--recover-after-restart'),
                    (True, False, '--no-recover-after-restart')):
                with self.subTest(saved=saved, requested=requested):
                    window = self.window(run)
                    window.saved_restart_recovery = saved
                    window.restart_recovery.setChecked(requested)
                    with patch.object(window, 'launch') as launch, patch.object(window, 'fail') as fail:
                        window.resume_run()
                    fail.assert_not_called()
                    launch.assert_called_once_with(['--run', str(run), '--resume', expected])


if __name__ == '__main__':
    unittest.main()
