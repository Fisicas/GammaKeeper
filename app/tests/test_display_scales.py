"""GUI-scale regression: transformations must not change scientific data."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen';os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import copy
import datetime as dt
import hashlib
import tempfile
import unittest
import warnings
from unittest.mock import patch
import numpy as np
from PySide6 import QtWidgets as Q
from matplotlib.colors import LogNorm,Normalize
from gui import Window,Charts,BG,PANEL,TEXT
from gui_analysis import SpectrumCanvas
from plot_scales import display_values,intensity_norm
from recorder import make_config
from storage import RunStore
from spectrum_view import latest_interval
from test_core import snapshot


class DisplayScales(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=Q.QApplication.instance() or Q.QApplication([])

    def setUp(self):
        parent=Path(__file__).resolve().parents[1]/'validation'/'display_temp'
        parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.root=Path(self.temp.name);self.windows=[]

    def tearDown(self):
        for window in self.windows:
            window.timer.stop();window.close()
            window.preview_pool.waitForDone(10000);window.accumulation_pool.waitForDone(10000);window.deleteLater()
        self.app.processEvents();self.temp.cleanup()

    def run_data(self,counts=(0,4,0,12,1),calibration=None):
        run=self.root/'run';store=RunStore(run,make_config('TEST',10,0,'demo','scale validation'))
        for second,values in ((0,[100]*len(counts)),(10,[100+c for c in counts])):
            s=snapshot(second,values)
            if calibration is not None:s['spectrum']['calibration_keV']=calibration
            store.append_snapshot(s)
        store.set_state('ui_status',dict(mode='demo',serial='TEST',state='stopped',pid=0,boot_id='boot',utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        store.set_state('recorder',dict(status='stopped',pid=0));store.close()
        return run

    def window(self,run):
        window=Window(run);window.timer.stop();self.windows.append(window)
        window.spectrum_mode.setCurrentIndex(0);window.tabs.setCurrentIndex(1);self.app.processEvents()
        return window

    def test_all_four_energy_count_combinations_and_roi_counts_unchanged(self):
        run=self.run_data();window=self.window(run)
        window.spectrum_roi_from.setValue(0);window.spectrum_roi_to.setValue(100)
        window.spectrum_roi_enabled.setChecked(True)
        before=copy.deepcopy(window.spectrum_canvas.display_row)
        for x,y in ((0,0),(1,0),(0,1),(1,1),(0,0)):
            window.spectrum_energy_axis.setCurrentIndex(x);window.spectrum_counts_axis.setCurrentIndex(y)
            with warnings.catch_warnings():
                warnings.simplefilter('error',UserWarning);window.spectrum_canvas.draw()
            self.assertEqual(window.spectrum_canvas.ax.get_xscale(),'log' if x else 'linear')
            self.assertEqual(window.spectrum_canvas.ax.get_yscale(),'log' if y else 'linear')
            self.assertEqual(window.spectrum_canvas.last_counts,[0,4,0,12,1])
            self.assertEqual(window.spectrum_canvas.last_roi_counts,16) # terminal excluded
            self.assertEqual(window.spectrum_canvas.display_row,before)
        np.testing.assert_array_equal(window.spectrum_canvas.ax.lines[0].get_ydata(),[0,4,0,12])

    def test_log_hides_zeros_and_linear_restores_them_without_pseudocounts(self):
        window=self.window(self.run_data())
        window.spectrum_counts_axis.setCurrentIndex(1)
        plotted=window.spectrum_canvas.ax.lines[0].get_ydata()
        np.testing.assert_array_equal(np.ma.getmaskarray(plotted),[True,False,True,False])
        self.assertIn('no pseudocount',window.spectrum_canvas.scale_note)
        window.spectrum_counts_axis.setCurrentIndex(0)
        np.testing.assert_array_equal(window.spectrum_canvas.ax.lines[0].get_ydata(),[0,4,0,12])

    def test_nonpositive_calibrated_energies_are_hidden_only_in_log_display(self):
        window=self.window(self.run_data(calibration=[-2.,2.,0.]))
        window.spectrum_energy_axis.setCurrentIndex(1)
        np.testing.assert_array_equal(np.ma.getmaskarray(window.spectrum_canvas.ax.lines[0].get_xdata()),[True,True,False,False])
        self.assertIn('Nonpositive energies',window.spectrum_canvas.scale_note)
        self.assertEqual(window.spectrum_canvas.last_counts,[0,4,0,12,1])
        window.spectrum_energy_axis.setCurrentIndex(0)
        np.testing.assert_array_equal(window.spectrum_canvas.ax.lines[0].get_xdata(),[-2,0,2,4])

    def test_all_zero_and_empty_log_views_have_valid_domains_without_warnings(self):
        window=self.window(self.run_data(counts=(0,0,0)))
        window.spectrum_energy_axis.setCurrentIndex(1);window.spectrum_counts_axis.setCurrentIndex(1)
        with warnings.catch_warnings():
            warnings.simplefilter('error',UserWarning);window.spectrum_canvas.draw();window.charts.draw()
        self.assertIn('No positive values',window.spectrum_canvas.scale_note)
        self.assertGreater(window.spectrum_canvas.ax.get_ylim()[0],0)
        window.prepare_new()
        with warnings.catch_warnings():
            warnings.simplefilter('error',UserWarning);window.spectrum_canvas.draw();window.charts.draw()
        self.assertIsNone(window.spectrum_canvas.last_counts)
        self.assertEqual(window.spectrum_canvas.ax.get_yscale(),'log')

    def test_axes_link_across_tabs_while_intensity_is_independent(self):
        window=self.window(self.run_data());window.analysis_rows=[latest_interval(window.run)]
        window.redraw_analysis()
        window.spectrum_energy_axis.setCurrentIndex(1);window.spectrum_counts_axis.setCurrentIndex(1)
        self.assertEqual(window.charts.spg.get_xscale(),'log')
        self.assertEqual(window.charts.count.get_yscale(),'log')
        window.intensity_scale.setCurrentIndex(0)
        self.assertIsInstance(window.charts.colorbar.mappable.norm,Normalize)
        self.assertNotIsInstance(window.charts.colorbar.mappable.norm,LogNorm)
        self.assertEqual(window.charts.count.get_yscale(),'log')
        window.energy_axis.setCurrentIndex(0);window.counts_axis.setCurrentIndex(0)
        self.assertEqual(window.spectrum_energy_axis.currentData(),'linear')
        self.assertEqual(window.spectrum_counts_axis.currentData(),'linear')
        self.assertEqual(window.charts.dose.get_yscale(),'linear')

    def test_spectrogram_linear_shows_zero_cells_log_masks_them(self):
        window=self.window(self.run_data());window.analysis_rows=[latest_interval(window.run)]
        window.intensity_scale.setCurrentIndex(0)
        mesh=window.charts.colorbar.mappable
        np.testing.assert_array_equal(np.ma.getmaskarray(mesh.get_array()),[[False]*5])
        self.assertEqual(float(mesh.get_array()[0,0]),0.)
        window.intensity_scale.setCurrentIndex(1)
        np.testing.assert_array_equal(np.ma.getmaskarray(window.charts.colorbar.mappable.get_array()),[[True,False,True,False,False]])

    def test_single_interval_time_axis_and_home_use_the_current_log_domain(self):
        window=self.window(self.run_data());row=latest_interval(window.run)
        window.analysis_rows=[row];window.spectrum_energy_axis.setCurrentIndex(1)
        window.spectrum_counts_axis.setCurrentIndex(1)
        window.spectrum_toolbar.home()
        self.assertGreater(window.spectrum_canvas.ax.get_xlim()[0],0)
        self.assertGreater(window.spectrum_canvas.ax.get_ylim()[0],0)
        import matplotlib.dates as dates
        end=dates.date2num(dt.datetime.fromtimestamp(row[0],dt.timezone.utc))
        start=end-row[2]['wall_duration_s']/86400
        np.testing.assert_allclose(window.charts.count.get_xlim(),[start,end],rtol=0,atol=1e-10)

    def test_scale_switch_does_not_reconstruct_touch_sources_or_launch_recorder(self):
        run=self.run_data();window=self.window(run)
        files=[run/'run.sqlite',run/'manifest.json']
        before=[hashlib.sha256(f.read_bytes()).hexdigest() for f in files]
        with patch('ui.spectrum_tab.latest_interval',side_effect=AssertionError('scale must reuse displayed data')),patch.object(window,'launch') as launch:
            window.spectrum_energy_axis.setCurrentIndex(1);window.spectrum_counts_axis.setCurrentIndex(1)
            launch.assert_not_called()
        self.assertEqual(before,[hashlib.sha256(f.read_bytes()).hexdigest() for f in files])

    def test_preferences_restore_and_close_save_the_actual_scales(self):
        saved=dict(display=dict(energy='log',counts='log',intensity='linear'))
        with patch('gui.gui_preferences.read',return_value=saved):window=self.window(self.run_data())
        self.assertEqual(window.spectrum_canvas.ax.get_xscale(),'log')
        self.assertEqual(window.spectrum_canvas.ax.get_yscale(),'log')
        self.assertEqual(window.intensity_scale.currentData(),'linear')
        with patch('gui.gui_preferences.save') as save:window.close()
        self.assertEqual(save.call_args.args[1]['display'],saved['display'])

    def test_masked_gaps_and_small_positive_values_are_preserved(self):
        source=np.ma.array([0.,1e-7,1.,5.],mask=[False,False,False,True]);original=source.copy()
        linear,norm=intensity_norm(source,'linear')
        self.assertFalse(linear.mask[0]);self.assertTrue(linear.mask[3])
        logarithmic,norm=intensity_norm(source,'log')
        self.assertEqual(norm.vmin,1e-7)
        np.testing.assert_array_equal(logarithmic.mask,[True,False,False,True])
        np.testing.assert_array_equal(source,original)
