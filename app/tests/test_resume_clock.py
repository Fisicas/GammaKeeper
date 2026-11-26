import datetime as dt
import json
from pathlib import Path
import sqlite3
from subprocess import CompletedProcess, TimeoutExpired
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from recorder import record
from run_policy import ClockReferenceUnavailable, ResumeCancelled, require_resume, require_resume_when_ready


class ResumeClockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name)
        self.now = dt.datetime(2026, 11, 25, tzinfo=dt.timezone.utc)
        self.measurement = dict(verified=True, reference_unix_s=self.now.timestamp(), reference_monotonic_s=99.)

    def config(self, remaining, time_basis='sntp_reference'):
        end = (self.now + dt.timedelta(seconds=remaining)).isoformat() if remaining is not None else None
        config = dict(mode='device', serial='CLOCK-TEST', interval_s=10, planned_end_utc=end,
                      schedule_time_basis=time_basis, created_boot_id='before-reboot',
                      deadline_monotonic_s=0., initial_clock_measurement={
                          **self.measurement, 'reference_unix_s': self.now.timestamp() - 3600})
        (self.run / 'manifest.json').write_text(json.dumps(config), encoding='utf-8')

    def result(self, measurement):
        return CompletedProcess([], 0, stdout=json.dumps(measurement).encode('utf-8'))

    def test_cross_boot_deadline_uses_the_new_verified_sample(self):
        with patch('run_policy.boot_identity', return_value='after-reboot'), patch('time.perf_counter', return_value=100.), \
                patch('run_policy.subprocess.run', return_value=self.result(self.measurement)):
            self.config(120)
            self.assertEqual(require_resume(self.run), self.measurement)
            self.config(0)
            with self.assertRaisesRegex(ValueError, 'original scheduled end') as expiry:
                require_resume(self.run)
            self.assertNotIsInstance(expiry.exception, ClockReferenceUnavailable)
        self.assertFalse((self.run / 'run.sqlite').exists())

    def test_continuous_run_has_no_sntp_preflight_dependency(self):
        self.config(None)
        with patch('run_policy.boot_identity', return_value='after-reboot'), patch('run_policy.subprocess.run') as query:
            self.assertIsNone(require_resume(self.run))
        query.assert_not_called()

    def test_automatic_resume_verifies_legacy_or_host_clock_physical_schedule(self):
        with patch('run_policy.boot_identity', return_value='after-reboot'), patch('time.perf_counter', return_value=100.), \
                patch('run_policy.resume_allowed', return_value=(True, '')):
            for basis in ('host_unverified', None):
                self.config(120, time_basis=basis)
                with self.subTest(basis=basis), patch('run_policy.subprocess.run', return_value=self.result(self.measurement)) as query:
                    self.assertIsNone(require_resume(self.run))
                    query.assert_not_called()
                    self.assertEqual(require_resume_when_ready(self.run), self.measurement)
                    query.assert_called_once()

    def test_invalid_or_unavailable_reference_is_retryable(self):
        self.config(120)
        bad_samples = [dict(verified=False), [], dict(verified=True),
                       {**self.measurement, 'reference_monotonic_s': -21.},
                       {**self.measurement, 'reference_unix_s': float('inf')}]
        with patch('run_policy.boot_identity', return_value='after-reboot'), patch('time.perf_counter', return_value=100.):
            for sample in bad_samples:
                with self.subTest(sample=sample), patch('run_policy.subprocess.run', return_value=self.result(sample)):
                    with self.assertRaises(ClockReferenceUnavailable):
                        require_resume(self.run)
            with patch('run_policy.subprocess.run', side_effect=TimeoutExpired('clock_check', 8)):
                with self.assertRaises(ClockReferenceUnavailable):
                    require_resume(self.run)

    def test_wait_retries_only_clock_errors(self):
        now = [0.]

        def sleep(seconds):
            now[0] += seconds

        with patch('run_policy.time.monotonic', side_effect=lambda: now[0]), patch('run_policy.time.sleep', side_effect=sleep):
            clock_error = ClockReferenceUnavailable('temporary')
            with patch('run_policy.require_resume', side_effect=[clock_error, clock_error, self.measurement]) as check:
                self.assertEqual(require_resume_when_ready(self.run, timeout=5), self.measurement)
            self.assertEqual(check.call_count, 3)
            self.assertEqual(now[0], 3.)
            with patch('run_policy.require_resume', side_effect=ValueError('expired')) as check:
                with self.assertRaisesRegex(ValueError, 'expired'):
                    require_resume_when_ready(self.run)
            check.assert_called_once()
            with patch('run_policy.require_resume', side_effect=clock_error) as check:
                with self.assertRaises(ClockReferenceUnavailable):
                    require_resume_when_ready(self.run, timeout=.5)
            check.assert_called_once()
            self.assertEqual(now[0], 3.5)

    def test_wait_can_be_cancelled_before_opening_the_run(self):
        with patch('run_policy.require_resume') as check:
            with self.assertRaises(ResumeCancelled):
                require_resume_when_ready(self.run, cancelled=lambda: True)
        check.assert_not_called()
        stop = self.run / 'stop.request'
        stop.touch()
        self.assertEqual(record(self.run, stop_request=stop, wait_for_resume_clock=True), 0)
        self.assertFalse((self.run / 'run.sqlite').exists())

    def test_recorder_reuses_the_verified_sample_and_rejects_an_old_boot_sample(self):
        stop = self.run / 'stop.request'
        stop.touch()
        with patch('run_policy.boot_identity', return_value='after-reboot'), \
                patch('recorder.boot_identity', return_value='after-reboot'), \
                patch('time.perf_counter', return_value=100.), patch('recorder.start_watcher', return_value=None), \
                patch('recorder.DeviceMutex'), patch('recorder.versions', return_value={}), \
                patch('recorder.WorkerClient') as client, \
                patch('recorder.clock_measurement', return_value=self.measurement) as recorder_query, \
                patch('run_policy.subprocess.run', return_value=self.result(self.measurement)) as policy_query:
            self.config(120)
            self.assertEqual(record(self.run, stop_request=stop), 0)
            policy_query.assert_called_once()
            recorder_query.assert_not_called()
            self.config(None)
            self.assertEqual(record(self.run, stop_request=stop), 0)
            policy_query.assert_called_once()
            recorder_query.assert_called_once()
        client.assert_not_called()
        connection = sqlite3.connect(self.run / 'run.sqlite')
        try:
            rows = connection.execute("SELECT payload FROM events WHERE kind='clock_measurement' ORDER BY id").fetchall()
        finally:
            connection.close()
        self.assertEqual([json.loads(payload) for (payload,) in rows], [self.measurement, self.measurement])


if __name__ == '__main__':
    unittest.main()
