"""Plot-first dashboard regression checks using only synthetic in-memory spectra."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['GAMMAKEEPER_TEST_MODE'] = '1'

import datetime as dt
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
from PySide6 import QtCore, QtGui, QtWidgets as Q
from PySide6.QtTest import QTest
from matplotlib.backend_bases import MouseEvent
from ui.charts import Charts
from ui.range_slider import RangeSlider
from gui import Window
from recorder import make_config
from storage import RunStore
from test_core import snapshot


class LiveDashboardPlots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])
        # Match the launcher's font setup: restricted offscreen Windows sessions
        # may not enumerate system fonts, which otherwise invalidates size checks.
        for name in ('segoeui.ttf','segoeuib.ttf','seguisb.ttf'):
            font=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'/name
            if font.exists():QtGui.QFontDatabase.addApplicationFontFromData(QtCore.QByteArray(font.read_bytes()))
        cls.app.setFont(QtGui.QFont('Segoe UI',10));cls.app.setStyle('Fusion')

    @staticmethod
    def temp_root():
        root = Path(__file__).resolve().parents[1] / 'validation' / 'test_temp_live_dashboard'
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_count_and_dose_share_time_but_use_separate_colored_axes(self):
        charts = Charts()
        self.assertIsNot(charts.count, charts.dose)
        self.assertTrue(charts.count.get_shared_x_axes().joined(charts.count, charts.dose))
        self.assertEqual(charts.count.get_ylabel(), 'Count rate (cps)')
        self.assertEqual(charts.dose.get_ylabel(), 'Dose rate (µSv/h)')
        self.assertEqual(charts.count.yaxis.get_label_position(), 'left')
        self.assertEqual(charts.dose.yaxis.get_label_position(), 'right')
        self.assertGreater(charts.dose.get_zorder(), charts.count.get_zorder())
        self.assertFalse(charts.dose.patch.get_visible())
        self.assertEqual(charts.count.yaxis.label.get_color(), '#43d8bf')
        self.assertEqual(charts.dose.yaxis.label.get_color(), '#f5bc66')
        self.assertIsNone(charts.count.get_legend())

        utc = 1791840000.0
        snapshot = dict(spectrum=dict(counts=[10, 10, 10, 10], calibration_keV=[0.0, 1.0, 0.0]))
        interval = dict(counts_delta=[2, 0, 5, 8], device_duration_s=10.0, wall_duration_s=10.0,
                        count_rate_cps=1.5, quality=['normal'])
        saved = dict(spectrum=snapshot['spectrum'], telemetry_bin={
            'coverage_fraction': 1.0, 'dose_rate_uSv_h': 0.05, 'device_count_rate_cps': 1.4})
        charts.show_rows([(utc, 0, interval, saved)], saved, 'device')
        self.assertEqual(len(charts.count.lines), 2)
        self.assertEqual(len(charts.dose.lines), 1)
        self.assertEqual(list(charts.dose.lines[0].get_ydata()), [0.05])
        charts.set_series_visible('estimator', False)
        self.assertFalse(charts.series_artists['estimator'][0].get_visible())

    def test_accumulated_plot_uses_supplied_original_channel_counts(self):
        charts = Charts()
        charts.energy_scale = 'linear'
        charts.spectrum_counts_scale = 'linear'
        counts = [12, 0, 31, 57]
        collection = dict(included_intervals=2, requested_start_utc=None,
                          selection_rule='Complete recorded intervals only')
        row = (1791840000.0, [0], dict(counts_delta=counts),
               dict(spectrum=dict(counts=counts, calibration_keV=[0.0, 1.0, 0.0]),
                    collection=collection))
        charts.set_accumulated(row)
        plotted = charts.accum.lines[0]
        self.assertEqual(list(plotted.get_ydata()), counts[:-1])
        self.assertIn('2 complete intervals', charts.accum.get_title(loc='left'))
        charts.roi=(1.,2.,False)
        charts.set_accumulated(row,status='cached checkpoint')
        self.assertIn('cached checkpoint',charts.accum.get_title(loc='left'))

    def test_full_intensity_range_and_equal_width_top_plots(self):
        charts=Charts();charts.resize(1200,700)
        charts.intensity_scale='linear'
        saved=dict(spectrum=dict(counts=[100]*6,calibration_keV=[5.,2.,0.]),
                   telemetry_bin={'coverage_fraction':0})
        def interval(peak):
            return dict(counts_delta=[1,3,peak,3,1,0],device_duration_s=10.,
                        wall_duration_s=10.,count_rate_cps=(peak+8)/10,quality=['normal'])
        charts.show_rows([(1791840000.,0,interval(21),saved),
                          (1791840010.,0,interval(12),saved)],saved,'demo')
        charts.draw()
        self.assertAlmostEqual(charts.spg.collections[0].norm.vmax,2.1)
        self.assertAlmostEqual(charts.spg.get_position().width,charts.accum.get_position().width,places=6)
        self.assertEqual(charts.colormap,'cividis')
        charts.show_rows([(1791840010.,0,interval(12),saved)],saved,'demo')
        self.assertAlmostEqual(charts.spg.collections[0].norm.vmax,2.1)
        charts.set_source('different run')
        charts.show_rows([(1791840010.,0,interval(12),saved)],saved,'demo')
        self.assertAlmostEqual(charts.spg.collections[0].norm.vmax,1.2)
        charts.color_percentile=90
        charts.show_rows([(1791840000.,0,interval(21),saved),
                          (1791840010.,0,interval(12),saved)],saved,'demo')
        self.assertLess(charts.spg.collections[0].norm.vmax,2.1)
        self.assertIn('clipped',charts.colorbar.ax.get_xlabel())

    def test_selected_time_range_accumulates_only_complete_intervals(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as scratch:
            run=Path(scratch)/'synthetic-range'
            store=RunStore(run,make_config('TEST',10,0,'demo','selected range'))
            for second,counts in ((0,[100,100]),(10,[101,100]),
                                  (20,[103,100]),(30,[106,100])):
                store.append_snapshot(snapshot(second,counts))
            store.set_state('ui_status',dict(mode='demo',serial='TEST',state='stopped',pid=0,
                                             boot_id='test',utc=dt.datetime.now(dt.timezone.utc).isoformat()))
            store.set_state('recorder',dict(status='stopped',pid=0,boot_id='test'))
            store.close()
            window=Window(run,read_only=True);window.timer.stop()
            try:
                first=int(dt.datetime(2026,1,1,tzinfo=dt.timezone.utc).timestamp())
                self.assertTrue(window.time_range_slider.isEnabled())
                window._update_time_domain(first+.25,first+30.75)
                self.assertEqual(window.time_range_slider.domain(),(first,first+31))
                window.apply_time_range()
                self.assertEqual(window.view_mode.currentData(),'window')
                window.view_from.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(first+20,QtCore.QTimeZone(0)))
                window.view_until.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(first+10,QtCore.QTimeZone(0)))
                window.apply_time_range()
                self.assertIn('increasing UTC bounds',window.plot_render_status.toolTip())
                window.view_from.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(first+5,QtCore.QTimeZone(0)))
                window.view_until.setDateTime(QtCore.QDateTime.fromSecsSinceEpoch(first+25,QtCore.QTimeZone(0)))
                window.apply_time_range()
                self.assertEqual(window.plot_control_error,'')
                self.assertEqual(window.view_mode.currentData(),'window')
                deadline=time.monotonic()+15
                while window.dashboard_accumulation_busy or window.dashboard_accumulation_row is None or window.dashboard_accumulation_key!=(
                        str(run.resolve()),first+5,first+25,None):
                    self.app.processEvents()
                    if time.monotonic()>deadline:self.fail(window.dashboard_accumulation_error or 'Selected accumulation did not finish')
                    time.sleep(.01)
                self.assertEqual(window.dashboard_accumulation_row[2]['counts_delta'],[2,0])
                collection=window.dashboard_accumulation_row[3]['collection']
                self.assertEqual(collection['included_intervals'],1)
                self.assertEqual(collection['excluded_boundary_intervals'],2)
                self.assertEqual(window.metrics[3].text(),'30 s')
                self.assertEqual(window.saved_spectra_label.text(),'Saved spectra: 4')
            finally:
                window.close();self.app.processEvents()

    def test_drag_ranges_axis_switch_and_fullscreen_plot_focus(self):
        window=Window(read_only=True);window.timer.stop()
        try:
            window.resize(1280,800);window.show();self.app.processEvents()
            counts=[1,4,16,4,1,0]
            row=(1791840000.,[0],dict(counts_delta=counts),
                 dict(spectrum=dict(counts=counts,calibration_keV=[5.,2.,0.]),
                      collection=dict(included_intervals=1,requested_start_utc=None,
                                      selection_rule='Complete recorded intervals only')))
            window.charts.set_accumulated(row)
            window.charts.draw()
            ax=window.charts.accum
            x1,y=ax.transData.transform((6.,4.))
            x2,_=ax.transData.transform((10.,4.))
            for name,x in (('button_press_event',x1),('motion_notify_event',x2),
                           ('button_release_event',x2)):
                event=MouseEvent(name,window.charts,x,y,button=1)
                window.charts.callbacks.process(name,event)
            self.assertTrue(window.roi_enabled.isChecked())
            self.assertAlmostEqual(window.roi_from.value(),6.,delta=.2)
            self.assertAlmostEqual(window.roi_to.value(),10.,delta=.2)
            self.assertEqual(window.energy_range_slider.values(),
                             window.spectrum_energy_slider.values())
            window.fit_spectrum_region(False)
            self.assertGreater(ax.get_ylim()[0],0)
            self.assertIsNotNone(window.charts.accum_view)
            window.reset_spectrum_axes()
            self.assertIsNone(window.charts.accum_view)
            original=window.spectrum_counts_axis.currentData()
            window.toggle_axis_from_pick(SimpleNamespace(artist=window.charts.accum.yaxis.label))
            self.assertNotEqual(window.spectrum_counts_axis.currentData(),original)
            window.toggle_fullscreen();self.app.processEvents()
            self.assertTrue(window.isFullScreen())
            self.assertTrue(all(frame.isHidden() for frame in window.metric_frames))
            self.assertEqual(window.tabs.currentIndex(),0)
            self.assertEqual(window.charts.grid.get_height_ratios(),[3.5,.65])
            window.exit_fullscreen();self.app.processEvents()
            self.assertFalse(window.isFullScreen())
            self.assertTrue(all(not frame.isHidden() for frame in window.metric_frames))
            self.assertEqual(window.charts.grid.get_height_ratios(),[2.1,1.1])
        finally:
            window.close();self.app.processEvents()

    def test_range_slider_drag_updates_both_handles_without_quantizing_to_ticks(self):
        slider=RangeSlider();slider.set_domain(0,100);slider.resize(400,32);slider.show()
        changes=[];slider.rangeCommitted.connect(lambda low,high:changes.append((low,high)))
        self.app.processEvents()
        QTest.mousePress(slider,QtCore.Qt.MouseButton.LeftButton,pos=QtCore.QPoint(12,16))
        QTest.mouseMove(slider,QtCore.QPoint(106,16))
        QTest.mouseRelease(slider,QtCore.Qt.MouseButton.LeftButton,pos=QtCore.QPoint(106,16))
        self.assertAlmostEqual(slider.values()[0],25,delta=1)
        self.assertEqual(slider.values()[1],100)
        self.assertEqual(changes[-1],slider.values())
        slider.close()

    def test_log_accumulated_spectrum_retains_full_energy_extent_after_live_redraws(self):
        charts = Charts()
        self.assertEqual(charts.energy_scale, 'log')
        self.assertEqual(charts.spectrum_counts_scale, 'log')
        self.assertEqual(charts.counts_scale, 'linear')
        channels=256
        counts=list(range(1,channels+1))
        calibration=[1.0,1.0,0.0]
        collection=dict(included_intervals=1,requested_start_utc=None,
                        selection_rule='Complete recorded intervals only')
        saved=dict(spectrum=dict(counts=counts,calibration_keV=calibration),
                   telemetry_bin={'coverage_fraction':0})
        interval=dict(counts_delta=counts,device_duration_s=10.0,wall_duration_s=10.0,
                      count_rate_cps=sum(counts)/10,quality=['normal'])
        row=(1791840000.0,[0],dict(counts_delta=counts),
             dict(spectrum=saved['spectrum'],collection=collection))
        charts.set_accumulated(row)
        for _ in range(3):
            charts.show_rows([(1791840000.0,0,interval,saved)],saved,'device')
            self.assertEqual(charts.accum.get_xscale(),'log')
            self.assertEqual(charts.accum.get_yscale(),'log')
            self.assertGreater(charts.accum.get_xlim()[1],250)
            self.assertLess(charts.accum.get_xlim()[1],300)

    def test_plot_scale_defaults_and_live_button_feedback(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as scratch:
            run=Path(scratch)/'empty-read-only-run'
            run.mkdir()
            window=Window(run,read_only=True)
            window.timer.stop()
            try:
                self.assertEqual(window.energy_axis.currentData(),'log')
                self.assertEqual(window.spectrum_energy_axis.currentData(),'log')
                self.assertEqual(window.counts_axis.currentData(),'linear')
                self.assertEqual(window.spectrum_counts_axis.currentData(),'log')
                self.assertEqual(window.spectrum_tab_counts_axis.currentData(),'log')
                self.assertEqual(window.intensity_scale.currentData(),'log')
                self.assertTrue(window.back_live_button.isEnabled())
                self.assertEqual(window.back_live_button.text(),'Refresh plots')
                with patch.object(window,'refresh_live_plots') as refresh:
                    window.back_live_button.click()
                refresh.assert_called_once_with()
                window.tabs.setCurrentIndex(1)
                self.assertEqual(window.back_live_button.text(),'Live plots')
                window.back_live_button.click()
                self.assertEqual(window.tabs.currentIndex(),0)
            finally:
                window.close()
                self.app.processEvents()

    def test_rendering_status_does_not_reflow_dashboard_plots(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as scratch:
            run=Path(scratch)/'empty-read-only-run'
            run.mkdir()
            window=Window(run,read_only=True)
            window.timer.stop()
            window.resize(1400,900)
            window.show()
            self.app.processEvents()
            try:
                self.assertIs(window.plot_render_status.parentWidget(),window.plot_refresh.parentWidget())
                self.assertTrue(window.plot_render_status.isHidden())
                self.assertEqual(window.plot_render_status.height(),24)
                charts_geometry=window.charts.geometry()
                toolbar_geometry=window.chart_toolbar.geometry()
                refresh_geometry=window.plot_refresh.geometry()

                window.preview_busy=True
                window.update_plot_render_status()
                self.app.processEvents()
                self.assertFalse(window.plot_render_status.isHidden())
                self.assertIn('Rendering spectrogram',window.plot_render_status.text())
                self.assertEqual(window.charts.geometry(),charts_geometry)
                self.assertEqual(window.chart_toolbar.geometry(),toolbar_geometry)
                self.assertEqual(window.plot_refresh.geometry(),refresh_geometry)

                window.preview_busy=False
                window.update_plot_render_status()
                self.app.processEvents()
                self.assertTrue(window.plot_render_status.isHidden())
                self.assertEqual(window.charts.geometry(),charts_geometry)
                self.assertEqual(window.chart_toolbar.geometry(),toolbar_geometry)
                self.assertEqual(window.plot_refresh.geometry(),refresh_geometry)
            finally:
                window.close()
                self.app.processEvents()

    def test_recording_shell_hides_setup_and_diagnostics_behind_controls(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as scratch:
            run = Path(scratch) / 'empty-read-only-run'
            run.mkdir()
            window = Window(run, read_only=True)
            self.assertTrue(window.setup_scroll.isHidden())
            self.assertTrue(window.tabs.tabBar().isHidden())
            self.assertTrue(window.details_container.isHidden())
            self.assertEqual(window.tabs.currentIndex(), 0)
            self.assertFalse(window.series_buttons['counts'].icon().isNull())
            self.assertFalse(window.series_buttons['estimator'].icon().isNull())
            window.settings_toggle.setChecked(True)
            self.assertFalse(window.setup_scroll.isHidden())
            window.close()
            self.app.processEvents()

    def test_large_live_metrics_and_header_controls_fit_smaller_windows(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as scratch:
            run=Path(scratch)/'empty-read-only-run';run.mkdir()
            window=Window(run,read_only=True);window.timer.stop()
            window.connection.setText('RECORDING')
            window.live_stop_button.setVisible(True)
            window.metrics[0].setText('12,345.67 cps')
            window.metrics[1].setText('1,234.567 µSv/h')
            window.metrics[3].setText('120,960')
            buttons=(window.settings_toggle,window.back_live_button,window.more_button,
                     window.fullscreen_button,window.live_stop_button)
            try:
                self.assertIn('https://github.com/Fisicas/GammaKeeper',window.brand_link.text())
                self.assertTrue(window.brand_link.openExternalLinks())
                for size in ((1280,800),(1920,1080)):
                    with self.subTest(size=size):
                        window.resize(*size);window.show();self.app.processEvents()
                        self.assertEqual(window.width(),size[0])
                        for index in (0,1,3):
                            metric=window.metrics[index]
                            self.assertGreaterEqual(metric.font().pointSizeF(),24)
                            self.assertGreaterEqual(metric.width(),metric.fontMetrics().horizontalAdvance(metric.text()))
                        visible=(window.brand_link,window.connection)+buttons
                        for widget in visible:
                            bounds=widget.geometry()
                            self.assertGreaterEqual(bounds.left(),0)
                            self.assertLessEqual(bounds.right(),window.centralWidget().width())
                        for first,second in zip(visible,visible[1:]):
                            self.assertFalse(first.geometry().intersects(second.geometry()))
                        for button in buttons:self.assertGreaterEqual(button.height(),36)
                        self.assertGreater(window.charts.height(),300)
                science_index=window.tabs.indexOf(window.experiment_page)
                self.assertGreater(science_index,0)
                with patch.object(window,'request_science') as refresh_qc:
                    window.tabs.setCurrentIndex(science_index)
                refresh_qc.assert_called_once_with()
            finally:
                window.close();self.app.processEvents()

    def test_live_accumulated_spectrum_loads_from_run_in_background(self):
        with tempfile.TemporaryDirectory(dir=self.temp_root()) as scratch:
            run = Path(scratch) / 'synthetic-run'
            store = RunStore(run, make_config('TEST', 10, 0, 'demo', 'dashboard accumulation'))
            store.append_snapshot(snapshot(0, [100, 200, 300]))
            store.append_snapshot(snapshot(10, [103, 207, 311]))
            store.set_state('ui_status', dict(mode='demo', serial='TEST', state='stopped', pid=0,
                                               boot_id='test', utc=dt.datetime.now(dt.timezone.utc).isoformat()))
            store.set_state('recorder', dict(status='stopped', pid=0, boot_id='test'))
            store.close()

            window = Window(run, read_only=True)
            window.timer.stop()
            try:
                self.assertFalse(window.plot_render_status.isHidden())
                deadline = time.monotonic() + 15
                while window.dashboard_accumulation_busy or window.dashboard_accumulation_row is None:
                    self.app.processEvents()
                    if time.monotonic() >= deadline:
                        self.fail(window.dashboard_accumulation_error or 'Dashboard accumulation did not finish')
                    time.sleep(.01)
                self.assertEqual(window.dashboard_accumulation_row[2]['counts_delta'], [3, 7, 11])
                self.assertIn('1 complete intervals', window.charts.accum.get_title(loc='left'))
                deadline = time.monotonic() + 15
                while window.preview_busy:
                    self.app.processEvents()
                    if time.monotonic() >= deadline:
                        self.fail('Dashboard preview did not finish')
                    time.sleep(.01)
                window.refresh()
                self.assertIn('Display: individual recorded intervals', window.plot_cadence.text())
                self.assertTrue(window.plot_render_status.isHidden())
            finally:
                window._closing = True
                window.dashboard_accumulation_cancel.set()
                window.dashboard_accumulation_pool.waitForDone(10000)
                window.preview_pool.waitForDone(10000)
                window.close()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
