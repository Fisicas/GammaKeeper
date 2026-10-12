import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen');os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import datetime as dt
import json
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6 import QtWidgets as Q
import annotations
from experiment_metadata import save_sheet,latest
from monitor import AlertPolicy,Monitor,read_monitor
from recorder import make_config
from storage import RunStore
from test_core import snapshot
from gui import Window
from scientific_qc import progress


class MonitorAndUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=Q.QApplication.instance() or Q.QApplication([])
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()
    def data(self,enabled=False):
        run=self.root/'recording';backup=self.root/'backups';backup.mkdir()
        config=make_config('TEST',10,0,'demo','SYNTHETIC UI')
        config['monitoring']=dict(enabled=enabled,notifications=False,backup_parent=str(backup),backup_interval_s=86400)
        store=RunStore(run,config)
        for i in range(4):store.append_snapshot(snapshot(i*10,[100+i]*1024))
        store.set_state('recorder',dict(status='stopped',pid=0,boot_id='test'))
        store.set_state('ui_status',dict(mode='demo',serial='TEST',state='stopped',pid=0,boot_id='test',utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        store.close();return run
    def test_alert_debounce_deduplication_and_recovery(self):
        policy=AlertPolicy();active={'disconnected':'offline'}
        self.assertEqual(policy.update(active,0),[])
        self.assertEqual(policy.update(active,119),[])
        self.assertEqual(len(policy.update(active,120)),1)
        self.assertEqual(policy.update(active,500),[])
        policy.update({},501)
        self.assertEqual(policy.update(active,502),[])
        self.assertEqual(len(policy.update(active,622)),1)
        self.assertEqual(len(policy.update({'integrity':'bad raw'},623)),1)
    def test_monitor_backs_up_without_any_gui_or_device(self):
        run=self.data(True);monitor=Monitor(run)
        try:
            monitor.poll();self.assertIsNotNone(monitor.job)
            self.assertEqual(monitor.job['script'],'backup_run.py')
            monitor.job['process'].wait(timeout=25);monitor.poll()
            health=read_monitor(run)
            self.assertTrue(health['last_backup']['verified'])
            self.assertEqual(health['last_backup']['snapshots'],4)
            self.assertTrue((Path(health['last_backup']['target'])/'BACKUP_COMPLETE.json').exists())
        finally:monitor.close()
    def test_sample_versions_ui_read_only_and_scientific_render(self):
        run=self.data();save_sheet(run,1,dict(sample_id='sample 1',charcoal_mass_g='25',geometry='fixed fixture'))
        save_sheet(run,2,dict(sample_id='sample 1',charcoal_mass_g='26',geometry='fixed fixture'))
        self.assertEqual(latest(run,'sample_sheet')['metadata']['sample_sheet']['charcoal_mass_g'],26)
        self.assertEqual(len(annotations.load(run)),2)
        window=Window(str(run),read_only=True)
        try:
            self.assertFalse(window.stop_button.isEnabled());self.assertFalse(window.sample_button.isEnabled())
            window.science_result=progress(run);window.draw_science();window.science_canvas.draw()
            self.assertEqual(len(window.science_figure.axes),3)
            window.folder.setText(str(self.root));window.scan_runs();self.assertEqual(window.runs_table.rowCount(),1)
            self.assertIn('Device exposure',window.experiment_overview.text())
            with patch.object(Q.QMessageBox,'question') as confirm:
                window.stop_recording();confirm.assert_not_called()
        finally:
            window._closing=True;window.preview_cancel.set();window.accumulation_cancel.set();window.science_cancel.set()
            window.preview_pool.waitForDone(10000);window.accumulation_pool.waitForDone(10000);window.science_pool.waitForDone(10000)
            window.timer.stop();window.notes_timer.stop();window.close();self.app.processEvents()

    def test_background_link_negative_net_and_shared_variance_are_explicit(self):
        run=self.data();background=self.root/'background'
        store=RunStore(background,make_config('BACKGROUND',10,0,'demo','synthetic background'))
        for i in range(4):store.append_snapshot(snapshot(i*10,[100+2*i]*1024))
        store.close()
        save_sheet(background,1,dict(sample_id='BG',geometry='same documented fixture'))
        save_sheet(run,1,dict(sample_id='sample',geometry='same documented fixture',background_path=str(background)))
        result=progress(run);row=result['rows'][0];bg=result['background'];name='Pb-214 352 region'
        self.assertTrue(bg['geometry_documented_match'])
        self.assertLess(row['net_rate_cps'][name],0)
        variance=row['window_counts'][name]/row['device_exposure_s']**2+bg['window_counts'][name]/bg['device_exposure_s']**2
        self.assertAlmostEqual(row['net_poisson_sigma_cps'][name]**2,variance)
        self.assertAlmostEqual(row['shared_background_variance_cps2'][name],bg['window_counts'][name]/bg['device_exposure_s']**2)
        config=json.loads((background/'manifest.json').read_text());config['label']='changed'
        (background/'manifest.json').write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError,'background manifest changed'):progress(run)
