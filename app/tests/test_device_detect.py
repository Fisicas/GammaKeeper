import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen');os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import unittest
from unittest.mock import patch
from PySide6 import QtWidgets as Q
from device_discovery import serials_from_paths
from ui.paths import SETTINGS_DIR, DATA_ROOT

YES, NO = Q.QMessageBox.StandardButton.Yes, Q.QMessageBox.StandardButton.No


class DeviceDetectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Q.QApplication.instance() or Q.QApplication([])

    def setUp(self):
        from gui import Window
        self.window = Window()
        self.checks = patch.object(self.window, '_device_checks_enabled', return_value=True)
        self.checks.start()

    def tearDown(self):
        self.checks.stop()
        self.window._closing = True; self.window.timer.stop(); self.window.notes_timer.stop()
        self.window.close(); self.app.processEvents()

    def found(self, *serials):
        return patch('device_discovery.connected_serials', return_value=list(serials))

    def test_serials_parse_from_interface_paths(self):
        paths = [r'\\?\usb#vid_0483&pid_f123#rc-103-000001#{a5dcbf10-6530-11d2-901f-00c04fb951ed}',
                 r'\\?\USB#VID_0483&PID_F123#RC-102-000002#{a5dcbf10-6530-11d2-901f-00c04fb951ed}',
                 r'\\?\usb#vid_0483&pid_f123#rc-103-000001#{a5dcbf10-6530-11d2-901f-00c04fb951ed}',
                 r'\\?\usb#vid_1234&pid_5678#other#{a5dcbf10-6530-11d2-901f-00c04fb951ed}']
        self.assertEqual(serials_from_paths(paths), ['RC-103-000001', 'RC-102-000002'])

    def test_detect_fills_one_device_and_leaves_several_for_the_user(self):
        self.window.serial.setText('')
        with self.found('RC-103-000001'):
            self.assertEqual(self.window.detect_device(), 'RC-103-000001')
        self.assertEqual(self.window.serial.text(), 'RC-103-000001')
        self.window.serial.setText('')
        with self.found('RC-103-000001', 'RC-103-000002'):
            self.assertIsNone(self.window.detect_device())
        self.assertEqual(self.window.serial.text(), '')
        with self.found('RC-103-000001', 'RC-103-000002'), patch.object(Q.QInputDialog, 'getItem', return_value=('RC-103-000002', True)):
            self.assertEqual(self.window.detect_device(interactive=True), 'RC-103-000002')
        with self.found(), patch.object(self.window, 'fail') as fail:
            self.assertIsNone(self.window.detect_device(interactive=True))
            fail.assert_called_once()

    def test_start_confirms_the_detector_on_usb(self):
        with self.found():
            with self.assertRaises(ValueError):
                self.window.confirm_device('')
        with self.found('RC-103-000001'):
            self.assertEqual(self.window.confirm_device('rc-103-000001'), 'rc-103-000001')
            self.assertEqual(self.window.confirm_device(''), 'RC-103-000001')
            with patch.object(Q.QMessageBox, 'question', return_value=YES):
                self.assertEqual(self.window.confirm_device('RC-103-999999'), 'RC-103-000001')
            with patch.object(Q.QMessageBox, 'question', side_effect=[NO, NO]):
                self.assertIsNone(self.window.confirm_device('RC-103-999999'))
            with patch.object(Q.QMessageBox, 'question', side_effect=[NO, YES]):
                self.assertEqual(self.window.confirm_device('RC-103-999999'), 'RC-103-999999')
        with self.found(), patch.object(Q.QMessageBox, 'question', return_value=YES):
            self.assertEqual(self.window.confirm_device('RC-103-999999'), 'RC-103-999999')

    def test_start_uses_detected_device_and_saves_preferences(self):
        self.window.serial.setText(''); self.window.demo.setChecked(False)
        with self.found('RC-103-000001'), patch.object(self.window, 'launch') as launch, \
                patch('gui_preferences.save') as save:
            self.window.start_new()
        args = launch.call_args.args[0]
        self.assertEqual(args[args.index('--serial') + 1], 'RC-103-000001')
        self.assertEqual(save.call_args.args[0], SETTINGS_DIR)
        self.assertEqual(save.call_args.args[1]['setup']['serial'], 'RC-103-000001')
        self.window.run = None

    def test_settings_live_beside_the_data(self):
        self.assertEqual(SETTINGS_DIR.parent, DATA_ROOT)


if __name__ == '__main__':
    unittest.main()
