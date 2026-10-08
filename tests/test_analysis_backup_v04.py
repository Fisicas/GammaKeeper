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
from storage import RunStore
from recorder import make_config
from test_core import snapshot
from preview import read_preview
from backup_run import backup
from run_policy import resume_allowed
from telemetry_analysis import LiveJournalTelemetry
from completion import seal


class DerivedViewsAndBackups(unittest.TestCase):
    def setUp(self):
        self.parent=Path(__file__).resolve().parents[1]/'validation'/'analysis_temp'
        self.parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=self.parent);self.root=Path(self.temp.name)

    def tearDown(self):
        assert self.root.resolve().parent==self.parent.resolve()
        self.temp.cleanup()

    def run_data(self,points=21):
        run=self.root/'run';store=RunStore(run,make_config('TEST',5,0,'demo','view fixture'))
        for i in range(points):store.append_snapshot(snapshot(i*5,[100+i,200+2*i,300+3*i]))
        store.set_state('recorder',dict(status='completed',pid=0,boot_id='test'));store.close()
        return run

    def test_display_aggregation_conserves_all_channels_and_durations(self):
        run=self.run_data()
        result=read_preview(run,'full',max_bins=4)
        self.assertLessEqual(len(result['rows']),8)
        total=[0,0,0];duration=0
        for utc,epoch,d,s in result['rows']:
            if d['counts_delta'] is not None:
                total=[a+b for a,b in zip(total,d['counts_delta'])];duration+=d['device_duration_s']
        self.assertEqual(total,[20,40,60]);self.assertEqual(duration,100)
        self.assertEqual(result['summary']['total_counts'],120)

    def test_cached_completed_preview_preserves_data_and_validation_date(self):
        run=self.run_data()
        first=read_preview(run,'full',max_bins=4,cache_dir=self.root/'cache')
        second=read_preview(run,'full',max_bins=4,cache_dir=self.root/'cache')
        self.assertTrue(second['cached']);self.assertEqual(first['source_verified_utc'],second['source_verified_utc'])
        self.assertEqual(first['summary'],second['summary'])

    def test_preview_rejects_modified_raw_input(self):
        run=self.run_data()
        file=sorted((run/'spectra').rglob('*.json'))[10]
        file.write_text('{}')
        with self.assertRaisesRegex(ValueError,'integrity'):
            read_preview(run,'full',max_bins=4)

    def test_interrupted_backup_resumes_frozen_horizon_and_never_overwrites(self):
        run=self.run_data()
        target=self.root/'backup'
        def interrupted(done,total):
            if done==2:raise RuntimeError('Synthetic interrupted copy')
        with self.assertRaises(RuntimeError):backup(run,target,interrupted)
        source=RunStore(run)
        source.append_snapshot(snapshot(105,[121,242,363]))
        source.close()
        result=backup(run,target)
        self.assertTrue(result['verified']);self.assertEqual(result['snapshots'],21)
        self.assertEqual(len(list((target/'spectra').rglob('*.json'))),21)
        self.assertFalse(resume_allowed(target)[0])
        raw=sorted((target/'spectra').rglob('*.json'))[10]
        raw.write_text('synthetic changed destination')
        with self.assertRaisesRegex(ValueError,'not overwritten'):backup(run,target)
        self.assertEqual(raw.read_text(),'synthetic changed destination')

    def test_backup_refuses_foreign_destination(self):
        run=self.run_data();target=self.root/'authored';target.mkdir();(target/'important.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError,'refusing'):backup(run,target)
        self.assertEqual((target/'important.txt').read_text(),'keep')

    def test_endpoint_clamp_and_checkpoint_match_reception_integral(self):
        run=self.run_data(2);store=RunStore(run)
        base=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc).timestamp()
        for second in (0,2,6,10):
            utc=dt.datetime.fromtimestamp(base+second,dt.timezone.utc).isoformat()
            store.append_telemetry(dict(utc_after=utc,connection_id='x',result=dict(raw_hex='',decode_warning=None,
                                   records=[dict(type='RealTimeData',dt=utc,dose_rate=1e-5,count_rate=10.)])))
        reader=LiveJournalTelemetry(base,end_utc=base+5)
        reader.restore_checkpoint(store.db,self.root/'analysis.sqlite');self.assertTrue(reader.update(store.db));reader.checkpoint(store.db)
        self.assertAlmostEqual(reader.dose_uSv,.1*5/3600);self.assertEqual(reader.coverage(),1.)
        restored=LiveJournalTelemetry(base,end_utc=base+5)
        self.assertTrue(restored.restore_checkpoint(store.db,self.root/'analysis.sqlite'));restored.update(store.db)
        self.assertEqual(reader.dose_uSv,restored.dose_uSv)
        store.close()

    def test_sealing_detects_changed_raw_telemetry_payload(self):
        run=self.run_data(2);store=RunStore(run)
        store.append_telemetry(dict(utc_after='2026-01-01T00:00:01+00:00',result=dict(records=[],raw_hex='00',decode_warning=None)))
        with store.db:store.db.execute("UPDATE telemetry SET payload=replace(payload,'00','01')")
        store.close()
        with self.assertRaisesRegex(ValueError,'telemetry payload hash'):
            seal(run,self.root/'tampered_telemetry_seal')


if __name__=='__main__':unittest.main()
