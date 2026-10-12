import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen');os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import json
import sqlite3
import struct
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
from radiacode.bytes_buffer import BytesBuffer
from radiacode.types import COMMAND, VSFR
from device import ReadoutDevice, SOUND_CLICKS
from runtime import python_command

ROOT = Path(__file__).resolve().parents[1]
ALARMS_AND_BUTTONS = 0b11111101  # every SOUND_CTRL flag except clicks


class FakeTransport:
    """Answers register reads/writes like the device; `keep=False` makes writes not stick."""
    def __init__(self, registers, keep=True):
        self.registers, self.keep, self.writes = dict(registers), keep, []

    def execute(self, request):
        reqtype, _, seq = struct.unpack_from('<HBB', request, 4)
        args = request[8:]
        header = request[4:8]
        if reqtype == int(COMMAND.RD_VIRT_SFR_BATCH):
            count, register = struct.unpack('<II', args)
            return BytesBuffer(header + struct.pack('<II', 1, self.registers[register]))
        if reqtype == int(COMMAND.WR_VIRT_SFR):
            register, value = struct.unpack('<II', args)
            self.writes.append((register, value))
            if self.keep:
                self.registers[register] = value
            else:
                self.keep = True  # first write is ignored; later ones (the restore) stick
            return BytesBuffer(header + struct.pack('<I', 1))
        raise AssertionError(f'unexpected command {reqtype}')


def device(transport):
    dev = object.__new__(ReadoutDevice)
    dev._seq, dev._permitted_write, dev._connection = 0, None, transport
    return dev


class ClickSoundDeviceTests(unittest.TestCase):
    def registers(self, sound_ctrl):
        return {int(VSFR.SOUND_CTRL): sound_ctrl, int(VSFR.SOUND_ON): 1}

    def test_only_the_click_bit_changes(self):
        transport = FakeTransport(self.registers(ALARMS_AND_BUTTONS))
        dev = device(transport)
        on = dev.set_click_sound(True)
        self.assertTrue(on['clicks'])
        self.assertEqual(transport.registers[int(VSFR.SOUND_CTRL)], ALARMS_AND_BUTTONS | SOUND_CLICKS)
        off = dev.set_click_sound(False)
        self.assertFalse(off['clicks'])
        self.assertEqual(transport.registers[int(VSFR.SOUND_CTRL)], ALARMS_AND_BUTTONS)
        self.assertNotIn('error', off)
        writes_before = len(transport.writes)
        dev.set_click_sound(False)  # already off: no write at all
        self.assertEqual(len(transport.writes), writes_before)

    def test_other_register_writes_stay_blocked(self):
        dev = device(FakeTransport(self.registers(0)))
        with self.assertRaises(PermissionError):
            dev.execute(COMMAND.WR_VIRT_SFR, struct.pack('<II', int(VSFR.SOUND_CTRL), 0xFF))
        with self.assertRaises(PermissionError):
            dev.execute(COMMAND.WR_VIRT_SFR, struct.pack('<II', int(VSFR.SOUND_ON), 0))
        self.assertIsNone(dev._permitted_write)

    def test_setting_not_kept_is_restored_exactly(self):
        transport = FakeTransport(self.registers(ALARMS_AND_BUTTONS), keep=False)
        result = device(transport).set_click_sound(True)
        self.assertIn('error', result)
        self.assertEqual(transport.registers[int(VSFR.SOUND_CTRL)], ALARMS_AND_BUTTONS)
        self.assertEqual(transport.writes[-1], (int(VSFR.SOUND_CTRL), ALARMS_AND_BUTTONS))


def events(run, kind):
    try:
        db = sqlite3.connect((run/'run.sqlite').as_uri()+'?mode=ro', uri=True, timeout=1)
    except sqlite3.Error:
        return []  # the recorder has not created its journal yet
    try:
        return [json.loads(p) for p, in db.execute('SELECT payload FROM events WHERE kind=? ORDER BY id', (kind,))]
    except sqlite3.Error:
        return []
    finally:
        db.close()


def wait_for(predicate, timeout=40):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(.2)
    return None


class ClickSoundRecorderTests(unittest.TestCase):
    def test_start_option_live_request_and_persistence(self):
        base = ROOT/'validation'/'click_sound_temp'; base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as folder:
            run = Path(folder)/'run'
            with open(Path(folder)/'recorder.log', 'wb') as log:
                process = subprocess.Popen([python_command(), '-X', 'utf8', str(ROOT/'recorder.py'), '--run', str(run), '--demo',
                                            '--seconds', '300', '--interval', '1', '--click-sound', 'off'],
                                           cwd=ROOT, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW)
                try:
                    changed = wait_for(lambda: events(run, 'device_sound_changed'))
                    self.assertTrue(changed, 'start option not applied')
                    self.assertEqual((changed[0]['desired'], changed[0]['reason'], changed[0]['clicks']), (False, 'connected', False))
                    request = run/'device_sound_request.json'
                    request.write_text(json.dumps(dict(request_id='r1', clicks=True)), encoding='utf-8')
                    live = wait_for(lambda: [e for e in events(run, 'device_sound_changed') if e['reason'] == 'request'])
                    self.assertTrue(live, 'live request not applied')
                    self.assertTrue(live[0]['clicks'])
                    self.assertEqual(len(events(run, 'device_sound_requested')), 1)
                    time.sleep(1.2)  # the same request id is never applied twice
                    self.assertEqual(len([e for e in events(run, 'device_sound_changed') if e['reason'] == 'request']), 1)
                    db = sqlite3.connect((run/'run.sqlite').as_uri()+'?mode=ro', uri=True)
                    try:
                        boot = json.loads(db.execute("SELECT value FROM state WHERE key='recorder'").fetchone()[0])['boot_id']
                    finally:
                        db.close()
                    (run/f'stop_{boot}.request').write_text('stop\n', encoding='utf-8')
                    self.assertEqual(process.wait(timeout=30), 0)
                finally:
                    if process.poll() is None:
                        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
                        process.wait(timeout=10)
            db = sqlite3.connect((run/'run.sqlite').as_uri()+'?mode=ro', uri=True)
            try:
                saved = json.loads(db.execute("SELECT value FROM state WHERE key='device_click_sound'").fetchone()[0])
            finally:
                db.close()
            self.assertEqual((saved['desired'], saved['request_id']), (True, 'r1'))


class ClickSoundGuiTests(unittest.TestCase):
    def test_toggle_writes_a_request_for_the_opposite_state(self):
        from PySide6 import QtWidgets as Q
        from gui import Window
        app = Q.QApplication.instance() or Q.QApplication([])
        with tempfile.TemporaryDirectory() as folder:
            window = Window()
            try:
                window.run = Path(folder)
                window.last_status = dict(device_sound=dict(clicks=True))
                with patch.object(window, 'active', return_value=True):
                    window.toggle_click_sound()
                request = json.loads((Path(folder)/'device_sound_request.json').read_text(encoding='utf-8'))
                self.assertIs(request['clicks'], False)
                window.update_sound_button(dict(device_sound=dict(clicks=False)), True)
                self.assertIn('off — turn on', window.sound_button.text())
            finally:
                window.run = None
                window._closing = True; window.timer.stop(); window.notes_timer.stop(); window.close(); app.processEvents()


if __name__ == '__main__':
    unittest.main()
