"""Windows session-end awareness for the background recorder and supervisor.

A hidden top-level window on its own thread receives WM_QUERYENDSESSION and WM_ENDSESSION
(message-only windows do not get these broadcasts). The owning process's main loop polls
this object; the window thread never touches the journal, so the single-writer rule holds.

- With a block reason (the recorder), a normal restart or sign-out is answered with FALSE:
  Windows lists GammaKeeper with that reason and the user can cancel or continue anyway.
  Critical shutdowns (ENDSESSION_CRITICAL) are never blocked.
- WM_ENDSESSION(TRUE) sets `ending` and waits up to `end_budget_s` for the main loop to call
  close() after its final durable writes; Windows may terminate the process once it returns.
"""
import ctypes
import queue
import threading
import uuid
from ctypes import wintypes

WM_CLOSE, WM_DESTROY = 0x0010, 0x0002
WM_QUERYENDSESSION, WM_ENDSESSION = 0x0011, 0x0016
ENDSESSION_CLOSEAPP, ENDSESSION_CRITICAL, ENDSESSION_LOGOFF = 0x00000001, 0x40000000, 0x80000000
CLASS_NAME = 'GammaKeeperSessionWatcher'  # prefix; each watcher registers its own class
RECORDER_BLOCK_REASON = ('GammaKeeper is recording spectra. Restarting now interrupts the run; '
                         'it can be resumed after you sign in again.')

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [('style', wintypes.UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE), ('hIcon', wintypes.HICON),
                ('hCursor', wintypes.HANDLE), ('hbrBackground', wintypes.HBRUSH),
                ('lpszMenuName', wintypes.LPCWSTR), ('lpszClassName', wintypes.LPCWSTR)]


def flag_names(flags):
    names = [name for bit, name in ((ENDSESSION_CLOSEAPP, 'closeapp'), (ENDSESSION_CRITICAL, 'critical'),
                                    (ENDSESSION_LOGOFF, 'logoff')) if flags & bit]
    return names or ['shutdown_or_restart']


class SessionEndWatcher:
    def __init__(self, title, block_reason=None, end_budget_s=4.0):
        self.title, self.block_reason, self.end_budget_s = title, block_reason, end_budget_s
        self.events = queue.Queue()           # (kind, details) for the main loop to record
        self.ending = threading.Event()       # WM_ENDSESSION(TRUE) received
        self.finished = threading.Event()     # main loop finished its durable shutdown
        self.hwnd = None
        self.available = False
        self.error = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name='session-end-watcher', daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def _run(self):
        try:
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            user32.DefWindowProcW.restype = LRESULT
            user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
            user32.RegisterClassW.restype = wintypes.ATOM
            user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                               ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                               wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.ShutdownBlockReasonCreate.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
            user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
            user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
            user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
            kernel32.GetModuleHandleW.restype = wintypes.HMODULE
            self._user32 = user32
            self._proc = WNDPROC(self._wndproc)  # keep a reference for the window's lifetime
            instance = kernel32.GetModuleHandleW(None)
            # A window class is per process; a unique name keeps each watcher's handler its own.
            self._class_name = f'{CLASS_NAME}.{uuid.uuid4().hex[:12]}'
            wc = WNDCLASSW(lpfnWndProc=self._proc, hInstance=instance, lpszClassName=self._class_name)
            if not user32.RegisterClassW(ctypes.byref(wc)):
                raise OSError(ctypes.get_last_error(), 'RegisterClassW failed')
            self.hwnd = user32.CreateWindowExW(0, self._class_name, self.title, 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd:
                raise OSError(ctypes.get_last_error(), 'CreateWindowExW failed')
            if self.block_reason and not user32.ShutdownBlockReasonCreate(self.hwnd, self.block_reason):
                self.events.put(('shutdown_block_reason_unavailable', dict(winerror=ctypes.get_last_error())))
            self.available = True
        except (AttributeError, OSError) as error:
            self.error = str(error)
        finally:
            self._ready.set()
        if not self.available:
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))

    def _wndproc(self, hwnd, message, wparam, lparam):
        if message == WM_QUERYENDSESSION:
            flags = (lparam or 0) & 0xFFFFFFFF
            block = bool(self.block_reason) and not flags & ENDSESSION_CRITICAL
            self.events.put(('host_shutdown_requested', dict(flags=flag_names(flags), blocked=block)))
            return 0 if block else 1
        if message == WM_ENDSESSION:
            if wparam:
                self.events.put(('host_shutdown', dict(flags=flag_names((lparam or 0) & 0xFFFFFFFF))))
                self.ending.set()
                self.finished.wait(self.end_budget_s)
            else:
                self.events.put(('host_shutdown_cancelled', {}))
            return 0
        if message == WM_DESTROY:
            self._user32.PostQuitMessage(0)
            return 0
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def drain(self):
        """Pending (kind, details) notifications, oldest first."""
        items = []
        while True:
            try:
                items.append(self.events.get_nowait())
            except queue.Empty:
                return items

    def close(self):
        """Release a waiting WM_ENDSESSION handler and remove the window (its block reason goes with it)."""
        self.finished.set()
        if self.available and self.hwnd:
            self._user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)


def start_watcher(title, block_reason=None, end_budget_s=4.0):
    """A running watcher, or None where Windows session messages are unavailable."""
    try:
        watcher = SessionEndWatcher(title, block_reason, end_budget_s)
    except Exception:  # never let shutdown awareness stop acquisition
        return None
    return watcher if watcher.available else None
