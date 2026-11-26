"""Exercise the run-specific reboot gate without scheduling a real Windows task."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime import python_command
from supervisor import interrupted_for_reboot, supervise
from supervisor_store import read_health
from system_time import boot_identity
from test_session_end import ROOT, journal, snapshots, wait_for


class RebootEligibility(unittest.TestCase):
    def test_only_a_pending_run_on_a_new_boot_is_eligible(self):
        run = ROOT/'validation'/'reboot_gate_temp'
        run.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=run) as directory:
            run = Path(directory)
            health = dict(status='running', system_boot_id='old-windows', recover_after_restart=True)
            recorder = dict(status='running', boot_id='old-recorder', system_boot_id='old-windows')
            self.assertFalse(interrupted_for_reboot(run, health, recorder, 'old-windows'))
            self.assertFalse(interrupted_for_reboot(run, health, recorder, None))
            self.assertFalse(interrupted_for_reboot(run, health, dict(recorder, system_boot_id=None), 'new-windows'))
            self.assertTrue(interrupted_for_reboot(run, health, recorder, 'new-windows'))
            self.assertTrue(interrupted_for_reboot(run, dict(health, status='host_shutdown'),
                            dict(recorder, status='interrupted_by_shutdown'), 'new-windows'))
            for reason, saved_health, saved_recorder in (
                ('idle', None, None),
                ('opt-out', dict(health, recover_after_restart=False), recorder),
                ('stopping', dict(health, status='stopping'), recorder),
                ('completed', health, dict(recorder, status='completed')),
                ('stopped', health, dict(recorder, status='stopped')),
                ('failed', dict(health, status='attention_required'), recorder),
            ):
                with self.subTest(reason=reason), self.assertRaises(ValueError):
                    interrupted_for_reboot(run, saved_health, saved_recorder, 'new-windows')
            stop = run/'stop_supervisor_prior.request'
            stop.touch()
            with self.assertRaisesRegex(ValueError, 'requested Stop'):
                interrupted_for_reboot(run, dict(health, stop_request=str(stop)), recorder, 'new-windows')
            stop.unlink()
            (run/'BACKUP_PLAN.json').touch()
            with self.assertRaisesRegex(ValueError, 'backup snapshot'):
                interrupted_for_reboot(run, health, recorder, 'new-windows')

    def test_expired_run_removes_task_without_launching(self):
        run = ROOT/'validation'/'reboot_gate_temp'
        run.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=run) as directory:
            run = Path(directory).resolve()
            (run/'manifest.json').write_text(json.dumps(dict(serial='SIMULATED', planned_end_utc='2000-01-01T00:00:00+00:00')))
            prior = dict(status='host_shutdown', recover_after_restart=True, system_boot_id='prior')
            recorder = dict(status='interrupted_by_shutdown', boot_id='recorder', system_boot_id='prior')
            with patch('supervisor.DeviceMutex'), patch('supervisor.read_health', return_value=prior), \
                    patch('supervisor.inspect_run', return_value=(recorder, None)), \
                    patch('supervisor.boot_identity', return_value='current'), \
                    patch('supervisor.reboot_task.remove') as remove, patch('supervisor.supervise_run') as launch:
                self.assertEqual(supervise(['--run', str(run), '--auto-resume']), 2)
            remove.assert_called_once_with(run)
            launch.assert_not_called()


class RebootHandoff(unittest.TestCase):
    def test_reboot_appends_to_same_demo_run_and_stop_disarms(self):
        if not boot_identity():
            self.skipTest('Windows boot identity is unavailable')
        base = ROOT/'validation'/'reboot_handoff_temp'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            root = Path(directory)
            run = root/'run'
            env = dict(os.environ, GAMMAKEEPER_TEST_MODE='1')
            processes = []
            try:
                with (root/'initial.log').open('wb') as log:
                    processes.append(subprocess.Popen(
                        [python_command(), '-X', 'utf8', str(ROOT/'supervisor.py'), '--run', str(run),
                         '--demo', '--seconds', '0', '--interval', '1', '--recover-after-restart'],
                        cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW))
                self.assertTrue(wait_for(lambda: snapshots(run) >= 3 and self.armed(run)))
                subprocess.run(['taskkill', '/PID', str(processes[-1].pid), '/T', '/F'],
                               capture_output=True, check=True, timeout=10)
                processes[-1].wait(timeout=10)

                n_before = snapshots(run)
                manifest = (run/'manifest.json').read_bytes()
                raw_hashes = {file: hashlib.sha256(file.read_bytes()).digest()
                              for file in (run/'spectra').rglob('*.json')}
                self.assertEqual(len(raw_hashes), n_before)

                # The real action reads both prior-OS boot IDs. Substitute only those in
                # this temporary demo's control records; spectra and the manifest stay intact.
                for filename, key in (('run.sqlite', 'recorder'), ('supervisor.sqlite', 'supervisor')):
                    db = sqlite3.connect(run/filename)
                    try:
                        with db:
                            saved = json.loads(db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()[0])
                            saved['system_boot_id'] = 'prior-test-boot'
                            db.execute('UPDATE state SET value=? WHERE key=?', (json.dumps(saved), key))
                    finally:
                        db.close()

                shown = root/'opened-run.txt'
                script = (
                    'import sys; from pathlib import Path; import supervisor; '
                    'supervisor.open_recovered_window = lambda run: Path(sys.argv[1]).write_text(str(run)); '
                    'sys.exit(supervisor.supervise(sys.argv[2:]))'
                )
                with (root/'recovered.log').open('wb') as log:
                    processes.append(subprocess.Popen(
                        [python_command(), '-X', 'utf8', '-c', script, str(shown), '--run', str(run), '--auto-resume'],
                        cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW))
                self.assertTrue(wait_for(lambda: snapshots(run) >= n_before + 2 and shown.is_file()))
                self.assertEqual(Path(shown.read_text()), run)
                state = read_health(run)
                self.assertEqual(state['system_boot_id'], boot_identity())
                Path(state['stop_request']).touch()
                self.assertEqual(processes[-1].wait(timeout=20), 0)
                self.assertFalse(self.armed(run))
                self.assertEqual(read_health(run)['status'], 'stopped')
                self.assertEqual((run/'manifest.json').read_bytes(), manifest)
                for file, expected in raw_hashes.items():
                    self.assertEqual(hashlib.sha256(file.read_bytes()).digest(), expected)
                self.assertEqual(journal(run, 'SELECT seq FROM snapshots ORDER BY seq'),
                                 [(seq,) for seq in range(snapshots(run))])
                first_resumed_file, = journal(run, f'SELECT file FROM snapshots WHERE seq={n_before}')[0]
                resumed = json.loads((run/first_resumed_file).read_text(encoding='utf-8'))
                self.assertTrue(resumed['had_disconnect'])
            finally:
                for process in processes:
                    if process.poll() is None:
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                       capture_output=True, timeout=10)
                        process.wait(timeout=10)

    @staticmethod
    def armed(run):
        try:
            return bool((read_health(run) or {}).get('restart_recovery_armed'))
        except sqlite3.Error:
            return False


if __name__ == '__main__':
    unittest.main()
