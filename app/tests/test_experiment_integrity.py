import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['GAMMAKEEPER_TEST_MODE'] = '1'
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
import datetime as dt
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import annotations
from backup_run import backup
from completion import seal
from device import UTCClock
from recorder import make_config
from storage import RunStore
from test_core import snapshot
from ui.notes_tab import NotesMixin


class ExperimentIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.zero = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()

    def tearDown(self):
        self.temp.cleanup()

    def data(self, reset=False, planned=30):
        run = self.root/'recording'
        config = make_config('TEST', 10, planned, 'demo', 'qualification')
        config['created_utc'] = annotations.iso(self.zero)
        config['planned_end_utc'] = annotations.iso(self.zero+planned)
        store = RunStore(run, config)
        for i in range(4):
            value = snapshot(i*10, [100+20*i]*3)
            if reset and i>=2:
                value = snapshot(i*10, [5+10*(i-2)]*3, duration=5+10*(i-2), spectrum_generation=1)
            store.append_snapshot(value)
        store.set_state('recorder', dict(status='completed'))
        store.close()
        return run

    def test_reset_is_intact_but_incomplete_and_tail_is_independently_checked(self):
        run = self.data(reset=True)
        result = seal(run, self.root/'validation')
        self.assertTrue(result['passed'])
        self.assertEqual(result['data_quality'], 'review_required')
        self.assertIn('spectrum_boundary_requires_review', result['assessment']['warnings'])
        self.assertEqual(result['assessment']['spectra']['invalid_interval_wall_s'], 10)

    def test_missing_tail_and_zero_count_exposure_are_reported(self):
        run = self.data(planned=100)
        result = seal(run, self.root/'validation')
        self.assertEqual(result['assessment']['schedule']['missing_tail_s'], 70)
        self.assertIn('missing_scheduled_tail', result['assessment']['warnings'])
        from run_quality import assess
        row = dict(end_utc=annotations.iso(self.zero+10), quality='normal', total_counts='0',
                   wall_duration_s='10', device_duration_s='10')
        config = dict(interval_s=10)
        self.assertEqual(assess(config, [row], {}, {}, 0)['spectra']['device_exposure_s'], 10)

    def test_corrected_seal_survives_host_clock_backstep_and_backup_keeps_notes(self):
        run = self.data()
        with patch('annotations.time.time', side_effect=[1000., 900.]):
            annotations.add(run, self.zero, 'seal', 'original')
            latest = annotations.add(run, self.zero-10, 'seal', 'corrected', metadata={'event_uncertainty_s':2})
        self.assertEqual(annotations.seal(annotations.load(run))['id'], latest)
        target = self.root/'backup'
        result = backup(run, target)
        self.assertEqual(result['notes_verification'], 'hash_verified')
        self.assertEqual(annotations.seal(annotations.load(target))['id'], latest)
        with self.assertRaisesRegex(ValueError, 'frozen'):
            annotations.add(target, self.zero, 'note')
        with (target/'annotations.csv').open('a') as handle: handle.write('tampered')
        with self.assertRaisesRegex(ValueError, 'annotation integrity'): backup(run, target)

    def test_unverified_clock_is_not_labeled_sntp(self):
        clock = UTCClock(dict(verified=False, offset_s=0.))
        fake = SimpleNamespace(last_status={'last_snapshot':{'clock':clock.metadata()}},
                               current_timeline_seconds=clock.seconds)
        self.assertEqual(NotesMixin.note_clock(fake)[1], 'host_clock')
