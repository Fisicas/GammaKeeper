import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import copy,csv,json,datetime as dt,tempfile,unittest
from telemetry_analysis import observation_from_batch,ObservationIndex,LiveJournalTelemetry
from storage import RunStore
from export_run import export_run
from test_core import snapshot

ROOT=Path(__file__).resolve().parents[1]

def batch(second,lead=2):
    base=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
    received=(base+dt.timedelta(seconds=second)).isoformat()
    device=(base+dt.timedelta(seconds=second+lead)).isoformat()
    return dict(utc_after=received,connection_id='test',result=dict(raw_hex='00',decode_warning=None,
        records=[dict(type='RealTimeData',dt=device,count_rate=10.,dose_rate=1e-5,
                      count_rate_err=1.,dose_rate_err=2.,flags=0,real_time_flags=0)]))

class TelemetryObservationTests(unittest.TestCase):
    def test_future_projection_is_preserved_and_not_discarded(self):
        raw=batch(1,lead=12)
        before=copy.deepcopy(raw)
        observation=observation_from_batch(raw)
        self.assertEqual(raw,before)
        self.assertEqual(observation['device_dt'],raw['result']['records'][0]['dt'])
        self.assertEqual(observation['dt'],raw['utc_after'])
        self.assertEqual(observation['timestamp_basis'],'host_reception_utc')
        self.assertEqual(observation['device_projection_lead_s'],12.)

    def test_reception_bins_do_not_impute_through_outage(self):
        rows=[observation_from_batch(batch(s)) for s in (0,1,2,9)]
        index=ObservationIndex(rows)
        start=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc).timestamp()
        result=index.bin(start,start+10)
        self.assertAlmostEqual(result['coverage_fraction'],.6)
        self.assertIsNone(result['integrated_dose_uSv'])

    def test_export_rebuilds_bad_old_dose_bins_without_changing_raws(self):
        test_root=ROOT/'validation'/'telemetry_test_temp'
        test_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as temporary:
            path=Path(temporary)
            self.assertEqual(path.resolve().parent,test_root.resolve())
            run=path/'run'
            store=RunStore(run,dict(interval_s=10,serial='TEST',mode='demo'))
            store.append_snapshot(snapshot(0,[100,200]))
            store.append_snapshot(dict(snapshot(10,[110,210]),telemetry_bin=dict(coverage_fraction=0,dose_rate_uSv_h=None)))
            for second in range(11):
                store.append_telemetry(batch(second,lead=20))
            hashes=[p.read_bytes() for p in sorted((run/'spectra').rglob('*.json'))]
            store.close()
            output,report=export_run(run)
            self.assertEqual(hashes,[p.read_bytes() for p in sorted((run/'spectra').rglob('*.json'))])
            with (output/'time_bins.csv').open(newline='',encoding='utf-8') as file:
                rows=list(csv.DictReader(file))
            self.assertEqual(float(rows[1]['telemetry_coverage_fraction']),1.)
            self.assertAlmostEqual(float(rows[1]['dose_rate_mean_uSv_h']),.1)
            self.assertEqual(float(rows[1]['original_snapshot_telemetry_coverage']),0.)
            self.assertEqual(rows[1]['telemetry_time_basis'],'host_reception_utc')

    def test_incremental_reader_matches_complete_bin_integral(self):
        import sqlite3
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE telemetry(id INTEGER PRIMARY KEY,payload TEXT)')
        for second in range(11):
            db.execute('INSERT INTO telemetry VALUES(?,?)',(second+1,json.dumps(batch(second))))
        start=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc).timestamp()
        reader=LiveJournalTelemetry(start,preview_seconds=3)
        self.assertFalse(reader.update(db,limit=4))
        self.assertFalse(reader.update(db,limit=4))
        self.assertTrue(reader.update(db,limit=4))
        self.assertEqual(reader.coverage(),1.)
        self.assertAlmostEqual(reader.dose_uSv,.1*10/3600)
        self.assertLessEqual(len(reader.records),8)
        db.close()

if __name__=='__main__':
    unittest.main(verbosity=2)
