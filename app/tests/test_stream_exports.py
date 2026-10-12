import os
os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
import numpy as np
import annotations
from recorder import make_config
from storage import RunStore
from test_core import snapshot
from time_resolved import export_series
from journal_observations import JournalObservationIndex
from telemetry_analysis import ObservationIndex, read_observations


class StreamingExports(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.zero=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc).timestamp()

    def tearDown(self): self.temp.cleanup()

    def data(self):
        run=self.root/'recording'
        store=RunStore(run,make_config('TEST',10,0,'demo','streamed'))
        for i in range(9): store.append_snapshot(snapshot(i*10,[100+i,200+2*i],had_disconnect=i==4))
        for t in [0,2,2,4,10,12]:
            utc=annotations.iso(self.zero+t)
            store.append_telemetry(dict(utc_after=utc,result=dict(records=[dict(type='RealTimeData',dt=utc,count_rate=10.,dose_rate=1e-5)])))
        store.close();return run

    def test_chunks_conserve_counts_and_isolate_recovered_aggregate(self):
        run=self.data();annotations.add(run,self.zero-5,'seal')
        target,report=export_series(run,self.root/'export',keep_aggregates=True,chunk_size=2)
        counts=np.zeros(2,dtype=np.int64);aggregates=0
        for entry in report['segments']:
            with np.load(target/(entry['stem']+'.npz')) as arrays:
                self.assertLessEqual(len(arrays['counts']),2)
                counts+=arrays['counts'].sum(axis=0)
                aggregates+=int(arrays['aggregate'].sum())
        np.testing.assert_array_equal(counts,[8,16])
        self.assertEqual(aggregates,1)
        self.assertEqual(report['time_zero_basis'],'seal')
        self.assertEqual(report['time_zero_note_id'],1)
        self.assertTrue((target/'annotations.csv').exists())
        self.assertEqual(len((target/'source_snapshots.jsonl').read_text().splitlines()),9)

    def test_acquisition_zero_is_explicit_and_sliding_aggregate_is_rejected(self):
        run=self.data();annotations.add(run,self.zero-5,'seal')
        _,report=export_series(run,self.root/'acquisition',time_zero_basis='acquisition')
        self.assertEqual(report['time_zero_utc'],dt.datetime.fromtimestamp(self.zero,dt.timezone.utc).isoformat())
        with self.assertRaisesRegex(ValueError,'nonoverlapping'):
            export_series(run,self.root/'bad',mode='sliding',width_s=20,step_s=10,keep_aggregates=True)
        self.assertFalse((self.root/'bad').exists())

    def test_streaming_telemetry_matches_reference_with_duplicates_and_gaps(self):
        run=self.data();db=sqlite3.connect(run/'run.sqlite')
        try:
            legacy=ObservationIndex(read_observations(db));stream=JournalObservationIndex(db)
            for a,b in [(0,5),(1,11),(0,20),(8,9)]:
                expected=legacy.bin(self.zero+a,self.zero+b);actual=stream.bin(self.zero+a,self.zero+b)
                for field in ['coverage_fraction','dose_rate_uSv_h','device_count_rate_cps','observed_integrated_dose_uSv']:
                    if expected[field] is None:self.assertIsNone(actual[field])
                    else:self.assertAlmostEqual(actual[field],expected[field])
        finally:db.close()

    def test_unknown_clock_uncertainty_is_not_replaced_by_a_known_maximum(self):
        run=self.root/'uncertain'
        store=RunStore(run,make_config('TEST',10,0,'demo','uncertain time'))
        for i in range(3):
            clock={'quality':'unverified','estimated_uncertainty_s':None} if i<2 else {'quality':'referenced','estimated_uncertainty_s':.2}
            store.append_snapshot(snapshot(i*10,[100+i],clock=clock))
        store.close()
        target,report=export_series(run,self.root/'export',mode='width',width_s=20)
        row=json.loads((target/(report['segments'][0]['stem']+'_bins.jsonl')).read_text().splitlines()[0])
        self.assertIsNone(row['clock_estimated_uncertainty_max_s'])
        self.assertEqual(row['clock_known_uncertainty_max_s'],.2)
        self.assertEqual(row['clock_uncertainty_missing_intervals'],1)
