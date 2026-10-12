import os
os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import hashlib
import math
import tempfile
import unittest
import numpy as np
from scipy.special import ndtr
from experiment_engine import ExperimentEngine
from scientific_models import peak_fit, decay_fit
from recorder import make_config
from storage import RunStore
from test_core import snapshot


class ScientificProgress(unittest.TestCase):
    def test_gain_model_recovers_known_shift_with_blend_and_refuses_no_information(self):
        channels=np.arange(1024);edges=3*(np.arange(1025)-.5)
        rng=np.random.default_rng(43)
        model=50000*(ndtr((edges[1:]-612.3)/15)-ndtr((edges[:-1]-612.3)/15))
        model+=10000*(ndtr((edges[1:]-586.2)/15)-ndtr((edges[:-1]-586.2)/15))+3
        fit=peak_fit(rng.poisson(model),[0,3,0],609.3,550,680,583.2)
        self.assertEqual(fit['status'],'qualified_advisory')
        self.assertAlmostEqual(fit['centroid_keV'],612.3,delta=.7)
        self.assertGreater(fit['centroid_sigma_keV'],0)
        self.assertEqual(peak_fit(np.zeros(1024),[0,3,0],609.3,550,680)['status'],'insufficient_information')

    def test_bin_integrated_joint_poisson_decay_and_shared_background(self):
        rng=np.random.default_rng(16);decay=math.log(2)/3.8222;rows=[]
        for i in range(3,243):
            a,b=i/24,(i+1)/24
            expected=3600*(20*np.exp(-decay*a)*(-np.expm1(-decay*(b-a)))/(decay*(b-a))+.5)
            rows.append(dict(start_utc=i*3600,end_utc=(i+1)*3600,device_exposure_s=3600,
                             quality=['normal'],window_counts={'test':int(rng.poisson(expected))}))
        fit=decay_fit(rows,'test',0,3,background=(18000,36000))
        self.assertEqual(fit['status'],'qualified_advisory')
        self.assertAlmostEqual(fit['half_life_d'],3.8222,delta=.04)
        self.assertGreater(fit['half_life_sigma_d'],0)
        self.assertIn('conditional',fit['uncertainty'])

    def test_incremental_cache_conserves_channels_and_detects_changed_endpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'recording'
            store=RunStore(run,make_config('TEST',10,0,'demo','cache'))
            for i in range(10):store.append_snapshot(snapshot(i*10,[100+i,200+2*i]))
            store.close()
            engine=ExperimentEngine(run,root/'cache.sqlite')
            while not engine.update(limit=2):pass
            result=engine.result()
            np.testing.assert_array_equal(sum((r['counts'] for r in result['rows'])),[9,18])
            self.assertEqual(result['summary']['device_elapsed_s'],90)
            engine.close()
            store=RunStore(run);store.append_snapshot(snapshot(100,[110,220]));store.close()
            engine=ExperimentEngine(run,root/'cache.sqlite');self.assertTrue(engine.update())
            np.testing.assert_array_equal(sum((r['counts'] for r in engine.result()['rows'])),[10,20])
            file=max((run/'spectra').glob('*/*.json'))
            file.write_text('{}')
            with self.assertRaisesRegex(ValueError,'endpoint integrity'):engine.update()
            engine.close()
