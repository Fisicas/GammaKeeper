"""Exercise tab opening/refresh directly, rather than calling the plot method."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import tempfile,json,datetime as dt,unittest
from PySide6 import QtWidgets as Q
from gui import Window
from storage import RunStore
from recorder import make_config
from test_core import snapshot
from spectrum_view import latest_interval


class SpectrumTab(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=Q.QApplication.instance() or Q.QApplication([])

    def setUp(self):
        self.parent=Path(__file__).resolve().parents[1]/'validation'/'spectrum_temp';self.parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=self.parent);self.root=Path(self.temp.name);self.windows=[]

    def tearDown(self):
        for window in self.windows:window.timer.stop();window.close();window.deleteLater()
        self.app.processEvents()
        assert self.root.resolve().parent==self.parent.resolve()
        self.temp.cleanup()

    def create(self,baseline_only=False):
        run=self.root/'run';store=RunStore(run,make_config('TEST',10,0,'demo','spectrum test'))
        store.append_snapshot(snapshot(0,[100,200,300]))
        if not baseline_only:store.append_snapshot(snapshot(10,[103,207,311]))
        store.set_state('ui_status',dict(mode='demo',serial='TEST',state='stopped',pid=0,boot_id='boot',
                                       utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        store.set_state('recorder',dict(status='stopped',pid=0,boot_id='boot'));store.close()
        return run

    def open(self,run):
        window=Window(run);window.timer.stop();self.windows.append(window)
        window.spectrum_mode.setCurrentIndex(0)
        return window

    def test_opening_tab_directly_draws_latest_difference_not_existing_counts(self):
        window=self.open(self.create());window.tabs.setCurrentIndex(1);self.app.processEvents()
        self.assertEqual(window.spectrum_canvas.last_counts,[3,7,11])
        self.assertTrue(window.spectrum_canvas.ax.lines)
        self.assertIn('21 counts',window.spectrum_note.text())

    def test_latest_interval_advances_without_a_heatmap_click(self):
        run=self.create();window=self.open(run);window.tabs.setCurrentIndex(1);self.app.processEvents()
        store=RunStore(run);store.append_snapshot(snapshot(20,[108,216,324]));store.close()
        window.refresh();self.app.processEvents()
        self.assertEqual(window.spectrum_canvas.last_counts,[5,9,13])

    def test_roi_controls_on_spectrum_tab_are_shared_and_change_counts(self):
        window=self.open(self.create());window.tabs.setCurrentIndex(1);self.app.processEvents()
        window.spectrum_roi_from.setValue(5);window.spectrum_roi_to.setValue(8)
        window.spectrum_roi_enabled.setChecked(True);self.app.processEvents()
        self.assertTrue(window.roi_enabled.isChecked())
        self.assertEqual(window.spectrum_canvas.last_roi_counts,10)
        self.assertIn('ROI 10 counts',window.spectrum_note.text())

    def test_selected_interval_is_retained_while_latest_advances(self):
        run=self.create();window=self.open(run)
        window.selected_analysis_row=latest_interval(run);window.spectrum_mode.setCurrentIndex(1)
        window.tabs.setCurrentIndex(1);self.app.processEvents()
        store=RunStore(run);store.append_snapshot(snapshot(20,[108,216,324]));store.close()
        window.refresh();self.app.processEvents()
        self.assertEqual(window.spectrum_canvas.last_counts,[3,7,11])

    def test_baseline_only_has_a_styled_explanation_and_no_invented_delta(self):
        window=self.open(self.create(True));window.tabs.setCurrentIndex(1);self.app.processEvents()
        self.assertIsNone(window.spectrum_canvas.last_counts)
        self.assertIn('baseline only',window.spectrum_note.text())
        self.assertNotEqual(window.spectrum_canvas.ax.get_facecolor(),(1.,1.,1.,1.))

    def test_source_reset_clears_previous_spectrum(self):
        window=self.open(self.create());window.tabs.setCurrentIndex(1);self.app.processEvents()
        window.prepare_new();self.app.processEvents()
        self.assertIsNone(window.selected_analysis_row)
        self.assertIsNone(window.spectrum_canvas.last_counts)


if __name__=='__main__':unittest.main()
