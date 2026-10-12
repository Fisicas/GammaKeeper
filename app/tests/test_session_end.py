import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen');os.environ['GAMMAKEEPER_TEST_MODE']='1'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import ctypes
import json
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from runtime import python_command
from run_policy import resume_allowed
from session_end import (ENDSESSION_CRITICAL, RECORDER_BLOCK_REASON, WM_ENDSESSION,
                         WM_QUERYENDSESSION, start_watcher)

ROOT = Path(__file__).resolve().parents[1]
user32 = ctypes.WinDLL('user32', use_last_error=True)
user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                       wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.ShutdownBlockReasonQuery.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]


def send(hwnd, message, wparam=0, lparam=0, timeout_ms=3000):
    result = ctypes.c_size_t()
    ok = user32.SendMessageTimeoutW(hwnd, message, wparam, lparam, 0x0002, timeout_ms, ctypes.byref(result))
    return bool(ok), result.value


def block_reason(hwnd):
    buffer, size = ctypes.create_unicode_buffer(512), wintypes.DWORD(512)
    return buffer.value if user32.ShutdownBlockReasonQuery(hwnd, buffer, ctypes.byref(size)) else None


def wait_for(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.2)
    return None


def journal(run, sql):
    db = sqlite3.connect((run/'run.sqlite').as_uri()+'?mode=ro', uri=True, timeout=1)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


def snapshots(run):
    try:
        return journal(run, 'SELECT count(*) FROM snapshots')[0][0]
    except sqlite3.Error:
        return 0


class WatcherTests(unittest.TestCase):
    def test_blocks_normal_restart_allows_critical_and_waits_for_close(self):
        watcher = start_watcher('GammaKeeper unit test', RECORDER_BLOCK_REASON, end_budget_s=3)
        self.assertIsNotNone(watcher)
        try:
            self.assertEqual(send(watcher.hwnd, WM_QUERYENDSESSION), (True, 0))
            self.assertEqual(send(watcher.hwnd, WM_QUERYENDSESSION, 0, ENDSESSION_CRITICAL), (True, 1))
            self.assertIn('GammaKeeper is recording', block_reason(watcher.hwnd))
            send(watcher.hwnd, WM_ENDSESSION, 0, 0)
            threading.Timer(.4, watcher.close).start()
            started = time.monotonic()
            send(watcher.hwnd, WM_ENDSESSION, 1, 0, timeout_ms=8000)
            self.assertGreaterEqual(time.monotonic()-started, .3)
            self.assertTrue(watcher.ending.is_set())
            kinds = [kind for kind, _ in watcher.drain()]
            self.assertEqual(kinds, ['host_shutdown_requested', 'host_shutdown_requested', 'host_shutdown_cancelled', 'host_shutdown'])
        finally:
            watcher.close()

    def test_no_block_reason_never_blocks(self):
        watcher = start_watcher('GammaKeeper unit test passive')
        try:
            self.assertEqual(send(watcher.hwnd, WM_QUERYENDSESSION), (True, 1))
            self.assertIsNone(block_reason(watcher.hwnd))
        finally:
            watcher.close()


class LivenessTests(unittest.TestCase):
    def test_live_pid_from_another_windows_boot_is_not_active(self):
        from PySide6 import QtWidgets as Q
        from gui import Window
        app = Q.QApplication.instance() or Q.QApplication([])
        window = Window()
        try:
            window.recorder = None
            window.last_status = dict(state='recording', pid=os.getpid(), system_boot_id='a-previous-boot')
            self.assertFalse(window.active())
            window.last_status = dict(state='recording', pid=os.getpid())
            self.assertTrue(window.active())  # older runs without a boot id keep the PID check
        finally:
            window._closing = True; window.timer.stop(); window.close(); app.processEvents()


class ProcessShutdownTests(unittest.TestCase):
    def setUp(self):
        base = ROOT/'validation'/'session_end_temp'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.run = Path(self.temp.name)/'run'
        self.processes = []
        self.logs = []

    def tearDown(self):
        for process in self.processes:
            if process.poll() is None:
                subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], capture_output=True)
            process.wait(timeout=10)
        for log in self.logs:
            log.close()
        self.temp.cleanup()

    def start(self, script):
        log = open(Path(self.temp.name)/(script+'.log'), 'wb')
        self.logs.append(log)
        process = subprocess.Popen([python_command(), '-X', 'utf8', str(ROOT/script), '--run', str(self.run), '--demo',
                                    '--seconds', '600', '--interval', '1'], cwd=ROOT, stdout=log, stderr=log,
                                   stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
        self.processes.append(process)
        return process

    def window(self, role):
        return wait_for(lambda: user32.FindWindowW(None, f'GammaKeeper {role} {self.run.name}'))

    def test_recorder_saves_final_snapshot_and_stays_resumable(self):
        recorder = self.start('recorder.py')
        self.assertTrue(wait_for(lambda: snapshots(self.run) >= 3), 'demo recorder produced no spectra')
        hwnd = self.window('recorder')
        self.assertTrue(hwnd, 'recorder session window not found')
        self.assertEqual(send(hwnd, WM_QUERYENDSESSION), (True, 0))  # normal restart: warned and blocked
        self.assertIn('Restarting now interrupts the run', block_reason(hwnd))
        before = snapshots(self.run)
        send(hwnd, WM_ENDSESSION, 1, 0, timeout_ms=10000)
        self.assertEqual(recorder.wait(timeout=20), 0)
        state = json.loads(journal(self.run, "SELECT value FROM state WHERE key='recorder'")[0][0])
        self.assertEqual(state['status'], 'interrupted_by_shutdown')
        self.assertTrue(state.get('system_boot_id'))
        kinds = [kind for kind, in journal(self.run, 'SELECT kind FROM events ORDER BY id')]
        for kind in ('session_end_watcher', 'host_shutdown_requested', 'host_shutdown', 'recorder_ended'):
            self.assertIn(kind, kinds)
        self.assertGreaterEqual(snapshots(self.run), before)
        self.assertEqual(resume_allowed(self.run), (True, ''))
        window = self.gui_window()
        try:
            window.refresh()
            self.assertIn('INTERRUPTED BY WINDOWS SHUTDOWN — resume available', window.connection.text())
            self.assertTrue(window.resume_button.isEnabled())
        finally:
            self.close_window(window)

    def gui_window(self):
        from PySide6 import QtWidgets as Q
        from gui import Window
        self.app = Q.QApplication.instance() or Q.QApplication([])
        return Window(str(self.run))

    def close_window(self, window):
        window._closing = True
        window.preview_cancel.set(); window.accumulation_cancel.set()
        window.preview_pool.waitForDone(10000); window.accumulation_pool.waitForDone(10000)
        window.timer.stop(); window.close()
        self.app.processEvents()

    def test_supervisor_does_not_restart_or_kill_the_recorder_at_session_end(self):
        supervisor = self.start('supervisor.py')
        self.assertTrue(wait_for(lambda: snapshots(self.run) >= 3), 'supervised demo recorder produced no spectra')
        recorder_window, supervisor_window = self.window('recorder'), self.window('supervisor')
        self.assertTrue(recorder_window and supervisor_window)
        self.assertEqual(send(supervisor_window, WM_QUERYENDSESSION), (True, 1))  # only the recorder blocks
        self.assertEqual(send(recorder_window, WM_QUERYENDSESSION), (True, 0))
        senders = [threading.Thread(target=send, args=(hwnd, WM_ENDSESSION, 1, 0, 10000))
                   for hwnd in (recorder_window, supervisor_window)]
        for thread in senders:
            thread.start()
        for thread in senders:
            thread.join(15)
        self.assertIsNotNone(wait_for(lambda: supervisor.poll() is not None, 20))
        db = sqlite3.connect((self.run/'supervisor.sqlite').as_uri()+'?mode=ro', uri=True)
        try:
            health = json.loads(db.execute("SELECT value FROM state WHERE key='supervisor'").fetchone()[0])
        finally:
            db.close()
        self.assertEqual(health['status'], 'host_shutdown')
        self.assertEqual(health['restarts'], 0)
        state = json.loads(journal(self.run, "SELECT value FROM state WHERE key='recorder'")[0][0])
        self.assertEqual(state['status'], 'interrupted_by_shutdown')
        started = [kind for kind, in journal(self.run, "SELECT kind FROM events WHERE kind='recorder_started'")]
        self.assertEqual(len(started), 1)


if __name__ == '__main__':
    unittest.main()
