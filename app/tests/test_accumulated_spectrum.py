import os
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import tempfile,datetime as dt,unittest,time,json
from PySide6 import QtWidgets as Q
from spectrum_view import AccumulatedSpectrum
from storage import RunStore
from recorder import make_config
from test_core import snapshot
from gui import Window


class CollectedSpectrum(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=Q.QApplication.instance() or Q.QApplication([])

    def setUp(self):
        self.parent=Path(__file__).resolve().parents[1]/'validation'/'accumulation_temp';self.parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=self.parent);self.root=Path(self.temp.name);self.windows=[]
        self.base=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc).timestamp()

    def tearDown(self):
        for window in self.windows:
            window.timer.stop();window.close();window.accumulation_pool.waitForDone(10000);window.preview_pool.waitForDone(10000);window.deleteLater()
        self.app.processEvents();assert self.root.resolve().parent==self.parent.resolve();self.temp.cleanup()

    def create(self,calibration_change=False,reset=False,gap=False):
        run=self.root/'run';store=RunStore(run,make_config('TEST',10,0,'demo','accumulated test'))
        for i in range(4):
            value=snapshot(i*10,[100+i,200+2*i,300+3*i])
            if calibration_change and i>=2:value['spectrum']['calibration_keV'][0]+=1
            if reset and i>=2:
                value=snapshot(i*10,[1+i,2+2*i,3+3*i],spectrum_generation=1)
            if gap and i==2:value['had_disconnect']=True
            store.append_snapshot(value)
        store.set_state('ui_status',dict(mode='demo',serial='TEST',state='stopped',pid=0,boot_id='boot',utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        store.set_state('recorder',dict(status='stopped',pid=0,boot_id='boot'));store.close();return run

    def finish(self,engine):
        while not engine.update(limit=2):pass
        return engine.result()

    def test_recording_total_excludes_existing_counts_and_updates_incrementally(self):
        run=self.create();engine=AccumulatedSpectrum(run);row=self.finish(engine)
        self.assertEqual(row[2]['counts_delta'],[3,6,9]);self.assertEqual(row[2]['device_duration_s'],30)
        store=RunStore(run);store.append_snapshot(snapshot(40,[104,208,312]));store.close()
        row=self.finish(engine);self.assertEqual(row[2]['counts_delta'],[4,8,12])
        self.assertEqual(row[3]['collection']['included_intervals'],4)

    def test_requested_range_only_sums_complete_intervals_and_reports_edges(self):
        run=self.create();row=self.finish(AccumulatedSpectrum(run,self.base+5,self.base+25))
        self.assertEqual(row[2]['counts_delta'],[1,2,3])
        metadata=row[3]['collection'];self.assertEqual(metadata['actual_start_utc'],self.base+10)
        self.assertEqual(metadata['actual_end_utc'],self.base+20);self.assertEqual(metadata['excluded_boundary_intervals'],2)

    def test_exact_range_boundaries_include_both_selected_intervals(self):
        row=self.finish(AccumulatedSpectrum(self.create(),self.base+10,self.base+30))
        self.assertEqual(row[2]['counts_delta'],[2,4,6]);self.assertEqual(row[3]['collection']['excluded_boundary_intervals'],0)

    def test_calibration_changes_are_not_silently_combined(self):
        run=self.create(calibration_change=True)
        with self.assertRaisesRegex(ValueError,'different calibrations'):self.finish(AccumulatedSpectrum(run))
        row=self.finish(AccumulatedSpectrum(run,epoch=1));self.assertEqual(row[2]['counts_delta'],[1,2,3])

    def test_reset_boundaries_are_not_counted_as_new_photons(self):
        row=self.finish(AccumulatedSpectrum(self.create(reset=True)))
        self.assertEqual(row[2]['counts_delta'],[2,4,6]);self.assertEqual(row[3]['collection']['epochs'],[0,1])
        self.assertEqual(row[3]['collection']['baseline_boundaries'],2)

    def test_recovered_gap_counts_are_retained_and_flagged(self):
        row=self.finish(AccumulatedSpectrum(self.create(gap=True)))
        self.assertEqual(row[2]['counts_delta'],[3,6,9])
        self.assertIn('gap_recovered_aggregate',row[3]['collection']['quality_counts'])

    def test_no_complete_intervals_and_invalid_range_are_explicit(self):
        run=self.create();self.assertIsNone(self.finish(AccumulatedSpectrum(run,self.base+11,self.base+19)))
        with self.assertRaises(ValueError):AccumulatedSpectrum(run,self.base+20,self.base+10)

    def test_gui_accumulated_modes_and_roi_show_recording_and_range(self):
        run=self.create();window=Window(run);window.timer.stop();self.windows.append(window)
        self.assertEqual(window.spectrum_mode.currentIndex(),2)
        window.tabs.setCurrentIndex(1);window.spectrum_mode.setCurrentIndex(2)
        deadline=time.monotonic()+20
        while window.accumulation_busy or window.spectrum_canvas.last_counts!=[3,6,9]:
            self.app.processEvents();time.sleep(.01)
            if time.monotonic()>deadline:self.fail(window.spectrum_note.text())
        window.collection_from.setDateTime(__import__('PySide6').QtCore.QDateTime.fromMSecsSinceEpoch(int((self.base+10)*1000),__import__('PySide6').QtCore.QTimeZone(0)))
        window.collection_until.setDateTime(__import__('PySide6').QtCore.QDateTime.fromMSecsSinceEpoch(int((self.base+30)*1000),__import__('PySide6').QtCore.QTimeZone(0)))
        window.collection_latest.setChecked(False);window.spectrum_mode.setCurrentIndex(3)
        deadline=time.monotonic()+20
        while window.accumulation_busy or window.spectrum_canvas.last_counts!=[2,4,6]:
            self.app.processEvents();time.sleep(.01)
            if time.monotonic()>deadline:self.fail(window.spectrum_note.text())
        window.spectrum_roi_from.setValue(5);window.spectrum_roi_to.setValue(8);window.spectrum_roi_enabled.setChecked(True)
        self.app.processEvents();self.assertEqual(window.spectrum_canvas.last_roi_counts,6)


if __name__=='__main__':unittest.main()
