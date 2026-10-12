import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen');os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import csv
import datetime as dt
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from PySide6 import QtWidgets as Q
import annotations
from export_run import export_run
from recorder import make_config
from storage import RunStore
from test_core import snapshot

ROOT = Path(__file__).resolve().parents[1]


class AnnotationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def setUp(self):
        base = ROOT/'validation'/'annotations_temp'
        base.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(dir=base))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_data(self):
        run = self.root/'run'
        store = RunStore(run, make_config('TEST', 10, 0, 'demo', 'notes test'))
        for second, values in ((0, [100, 100, 100]), (10, [104, 208, 312]), (20, [110, 300, 400])):
            store.append_snapshot(snapshot(second, values))
        store.set_state('ui_status', dict(mode='demo', serial='TEST', state='stopped', pid=0, boot_id='boot',
                                          utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        store.set_state('recorder', dict(status='stopped', pid=0))
        store.close()
        return run

    def test_append_only_and_latest_entered_seal_is_time_zero(self):
        run = self.run_data()
        first = annotations.add(run, 1000.0, 'seal', 'first mark')
        annotations.add(run, 1500.0, 'note', 'charcoal 25 g')
        corrected = annotations.add(run, 900.0, 'seal', 'corrected: sealed earlier', 'entered_by_user')
        notes = annotations.load(run)
        self.assertEqual([n['utc'] for n in notes], [900.0, 1000.0, 1500.0])
        self.assertEqual(annotations.seal(notes)['id'], corrected)
        db = sqlite3.connect(run/annotations.FILE)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute('UPDATE notes SET utc=0 WHERE id=?', (first,))
            with self.assertRaises(sqlite3.DatabaseError):
                db.execute('DELETE FROM notes')
        finally:
            db.close()
        with self.assertRaises(ValueError):
            annotations.add(run, 1.0, 'not-a-kind')
        with self.assertRaises(ValueError):
            annotations.add(self.root, 1.0, 'note')  # not a run folder
        self.assertEqual(annotations.load(self.root), [])

    def test_export_includes_notes_csv(self):
        run = self.run_data()
        annotations.add(run, 1000.0, 'seal', 'sealed')
        target, report = export_run(run, self.root/'export')
        self.assertEqual(report['annotations'], 1)
        with open(target/'annotations.csv', newline='', encoding='utf-8') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]['kind'], 'seal')
        self.assertEqual(rows[0]['current_seal'], 'True')

    def test_gui_marks_seal_shows_marker_and_sets_export_time_zero(self):
        from gui import Window
        run = self.run_data()
        window = Window(str(run))
        try:
            with patch.object(Q.QMessageBox, 'question', return_value=Q.QMessageBox.StandardButton.Yes):
                window.mark_seal()
            notes = annotations.load(run)
            self.assertEqual([n['kind'] for n in notes], ['seal'])
            self.assertIn('Seal time (time zero)', window.seal_summary.text())
            self.assertEqual(window.notes_table.rowCount(), 1)
            self.assertEqual(window.charts.annotations[0][1], 'seal')
            window.export_custom_zero.setChecked(False)
            from time_export_ui import use_seal_time
            use_seal_time(window)
            self.assertTrue(window.export_custom_zero.isChecked())
            self.assertAlmostEqual(window.export_time_zero.dateTime().toMSecsSinceEpoch()/1000, notes[0]['utc'], places=2)
            utc = notes[0]['utc']
            starts, ends = [window.charts.spg.get_ylim()[0]], [window.charts.spg.get_ylim()[1]]
            import matplotlib.dates as mdates
            x = mdates.date2num(dt.datetime.fromtimestamp(utc, dt.timezone.utc))
            before = len(window.charts.spg.lines)
            window.charts._draw_annotations([x - 1], [x + 1])
            self.assertEqual(len(window.charts.spg.lines), before + 1)
        finally:
            window._closing = True
            window.preview_pool.waitForDone(10000); window.accumulation_pool.waitForDone(10000)
            window.timer.stop(); window.notes_timer.stop(); window.close()
            self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
