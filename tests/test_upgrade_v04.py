import os
os.environ['RADIALOG_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import datetime as dt
import hashlib
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from device import UTCClock
from clock_check import select_measurements
from run_policy import resume_allowed
from recorder import record,make_config
from storage import RunStore
from supervisor import supervise,StreamWatchdog,save_supervisor_state
from saved_status import load_status
from completion import seal
from test_core import snapshot


class TimeAndStreams(unittest.TestCase):
    def measurement(self,mono,utc):
        return dict(verified=True,reference_monotonic_s=mono,reference_unix_s=utc,
                    offset_s=.05,estimated_uncertainty_s=.01,source_count=2)

    def test_wall_step_and_negative_correction_cannot_reverse_recorded_utc(self):
        with patch('device.time.perf_counter',return_value=100.),patch('device.time.time',return_value=1.):
            clock=UTCClock(self.measurement(100.,1000.))
        with patch('device.time.perf_counter',return_value=150.),patch('device.time.time',return_value=9000.):
            self.assertEqual(clock.seconds(),1050.)
            self.assertEqual(clock.update(self.measurement(150.,1049.5)),0.)
            self.assertEqual(clock.seconds(),1050.)
        with patch('device.time.perf_counter',return_value=151.):
            self.assertAlmostEqual(clock.seconds(),1050.995)
            self.assertEqual(clock.metadata()['quality'],'slewing')

    def test_network_failure_preserves_anchor_and_exposes_holdover(self):
        with patch('device.time.perf_counter',return_value=100.):
            clock=UTCClock(self.measurement(100.,1000.))
        with patch('device.time.perf_counter',return_value=200.):
            clock.update(dict(verified=False,note='network unavailable'))
            self.assertEqual(clock.seconds(),1100.)
            metadata=clock.metadata()
            self.assertEqual(metadata['quality'],'holdover')
            self.assertFalse(metadata['verified'])
            self.assertGreater(metadata['estimated_uncertainty_s'],.01)

    def test_restart_during_network_outage_preserves_same_boot_reference(self):
        with patch('device.time.perf_counter',return_value=100.),patch('device.boot_identity',return_value='same'):
            previous=UTCClock(self.measurement(100.,1000.)).metadata()
        with patch('device.time.perf_counter',return_value=200.),patch('device.time.time',return_value=9000.),patch('device.boot_identity',return_value='same'):
            clock=UTCClock(dict(verified=False,offset_s=0.,previous_clock=previous))
            self.assertEqual(clock.seconds(),1100.)
            self.assertEqual(clock.metadata()['quality'],'holdover')
        with patch('device.time.perf_counter',return_value=200.),patch('device.time.time',return_value=9000.),patch('device.boot_identity',return_value='different'):
            clock=UTCClock(dict(verified=False,offset_s=0.,previous_clock=previous))
            self.assertEqual(clock.seconds(),9000.)
            self.assertEqual(clock.metadata()['quality'],'unverified')

    def test_bad_reference_jump_is_rejected_without_stopping_capture(self):
        with patch('device.time.perf_counter',return_value=100.):
            clock=UTCClock(self.measurement(100.,1000.))
            clock.update(self.measurement(100.,1010.))
            self.assertEqual(clock.seconds(),1000.)
            self.assertEqual(clock.metadata()['quality'],'holdover')

    def test_servers_must_agree_and_single_source_is_visible(self):
        values=[dict(offset_s=.05,estimated_uncertainty_s=.012,server='one'),
                dict(offset_s=.4,estimated_uncertainty_s=.02,server='two')]
        self.assertFalse(select_measurements(values)['verified'])
        result=select_measurements(values[:1])
        self.assertTrue(result['verified'])
        self.assertEqual(result['source_count'],1)

    def test_advancing_telemetry_or_events_cannot_mask_stalled_spectra(self):
        watch=StreamWatchdog(5,now=0)
        self.assertIsNone(watch.observe((0,1,0),'recording',0))
        self.assertIsNone(watch.observe((3,21,0),'recording',20))
        self.assertEqual(watch.observe((20,42,0),'recording',41),'spectrum')

    def test_advancing_spectra_cannot_mask_stalled_telemetry(self):
        watch=StreamWatchdog(5,now=0)
        watch.observe((0,1,0),'recording',0)
        self.assertEqual(watch.observe((20,1,8),'recording',41),'telemetry')

    def test_reconnect_backoff_and_long_spectrum_interval_are_respected(self):
        watch=StreamWatchdog(600,now=0)
        self.assertIsNone(watch.observe((0,1,0),'recording',0))
        self.assertIsNone(watch.observe((4,601,0),'recording',600))
        for now in (660,720,780):
            self.assertIsNone(watch.observe((now,601,0),'reconnecting',now))


class CompletionAndOwnership(unittest.TestCase):
    def setUp(self):
        self.parent=Path(__file__).resolve().parents[1]/'validation'/'upgrade_temp'
        self.parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=self.parent)
        self.path=Path(self.temp.name)

    def tearDown(self):
        assert self.path.resolve().parent==self.parent.resolve()
        self.temp.cleanup()

    def finished(self):
        run=self.path/'run'
        config=make_config('TEST',10,1,'demo','finished fixture')
        config['planned_end_utc']='2026-01-01T00:00:00+00:00'
        store=RunStore(run,config)
        store.append_snapshot(snapshot(0,[100,200]))
        store.append_snapshot(snapshot(10,[103,207]))
        store.set_state('recorder',dict(status='completed',pid=0,boot_id='test'))
        store.close()
        return run

    def test_expired_resume_cannot_change_a_completed_run_from_either_entrypoint(self):
        run=self.finished()
        before={name:hashlib.sha256((run/name).read_bytes()).hexdigest() for name in ('run.sqlite','manifest.json')}
        self.assertFalse(resume_allowed(run)[0])
        self.assertEqual(record(run),2)
        self.assertEqual(supervise(['--run',str(run),'--resume']),2)
        self.assertFalse((run/'supervisor.sqlite').exists())
        self.assertEqual(before,{name:hashlib.sha256((run/name).read_bytes()).hexdigest() for name in before})

    def test_supervisor_reporting_does_not_write_or_lock_acquisition_database(self):
        run=self.finished()
        before=hashlib.sha256((run/'run.sqlite').read_bytes()).hexdigest()
        db=sqlite3.connect(run/'run.sqlite')
        try:
            db.execute('BEGIN IMMEDIATE')
            save_supervisor_state(run,dict(status='stopped',pid=0,restarts=0))
            self.assertTrue((run/'supervisor.sqlite').exists())
        finally:
            db.rollback();db.close()
        self.assertEqual(before,hashlib.sha256((run/'run.sqlite').read_bytes()).hexdigest())

    def test_completion_export_checks_every_channel_and_preserves_source(self):
        run=self.finished()
        before=(run/'manifest.json').read_bytes()
        report=seal(run,self.path/'seal')
        self.assertTrue(report['passed'])
        self.assertEqual(report['total_counts'],10)
        self.assertEqual(report['snapshots'],2)
        self.assertEqual(before,(run/'manifest.json').read_bytes())

    def test_failed_completion_never_changes_acquisition_status(self):
        run=self.finished()
        raw=next((run/'spectra').rglob('*.json'))
        raw.write_text('{}')
        with self.assertRaises(ValueError):
            seal(run,self.path/'bad_seal')
        verdict=json.loads((self.path/'bad_seal'/'VALIDATION.json').read_text())
        self.assertFalse(verdict['passed'])
        db=sqlite3.connect(run/'run.sqlite')
        self.assertEqual(json.loads(db.execute("SELECT value FROM state WHERE key='recorder'").fetchone()[0])['status'],'completed')
        db.close()


if __name__=='__main__':
    unittest.main()
