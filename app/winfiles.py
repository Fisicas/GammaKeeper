"""Read-only Windows process liveness for GUI recovery controls."""
import ctypes as c
from ctypes import wintypes as w


def process_alive(pid):
    api = c.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [w.DWORD,w.BOOL,w.DWORD]
    api.OpenProcess.restype = w.HANDLE
    api.WaitForSingleObject.argtypes = [w.HANDLE,w.DWORD]
    api.CloseHandle.argtypes = [w.HANDLE]
    handle = api.OpenProcess(0x00100000, False, int(pid))
    if not handle:
        return False
    try:
        return api.WaitForSingleObject(handle, 0) == 258
    finally:
        api.CloseHandle(handle)
