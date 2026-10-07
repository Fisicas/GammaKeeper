"""Windows bulk ReadFile/WriteFile transport for the installed Phyton driver.

Uses standard overlapped file I/O, not guessed private IOCTLs. No driver changes.
Device paths are enumerated with SetupAPI, never silently selected by order.
"""
import ctypes as c
from ctypes import wintypes as w
import struct
import time
import uuid


class GUID(c.Structure):
    _fields_ = [('Data1', w.DWORD), ('Data2', w.WORD), ('Data3', w.WORD), ('Data4', c.c_ubyte * 8)]


class InterfaceData(c.Structure):
    _fields_ = [('cbSize', w.DWORD), ('InterfaceClassGuid', GUID), ('Flags', w.DWORD), ('Reserved', c.c_size_t)]


class Overlapped(c.Structure):
    _fields_ = [('Internal', c.c_size_t), ('InternalHigh', c.c_size_t), ('Offset', w.DWORD), ('OffsetHigh', w.DWORD), ('hEvent', w.HANDLE)]


def device_paths():
    api = c.WinDLL('setupapi', use_last_error=True)
    api.SetupDiGetClassDevsW.argtypes = [c.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD]
    api.SetupDiGetClassDevsW.restype = w.HANDLE
    api.SetupDiEnumDeviceInterfaces.argtypes = [w.HANDLE, c.c_void_p, c.POINTER(GUID), w.DWORD, c.POINTER(InterfaceData)]
    api.SetupDiGetDeviceInterfaceDetailW.argtypes = [w.HANDLE, c.POINTER(InterfaceData), c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p]
    api.SetupDiDestroyDeviceInfoList.argtypes = [w.HANDLE]
    guid = GUID.from_buffer_copy(uuid.UUID('a5dcbf10-6530-11d2-901f-00c04fb951ed').bytes_le)
    handle = api.SetupDiGetClassDevsW(c.byref(guid), None, None, 0x12)
    if handle == c.c_void_p(-1).value:
        raise c.WinError(c.get_last_error())
    paths = []
    try:
        for i in range(1024):
            data = InterfaceData(cbSize=c.sizeof(InterfaceData))
            if not api.SetupDiEnumDeviceInterfaces(handle, None, c.byref(guid), i, c.byref(data)):
                if c.get_last_error() == 259:
                    break
                raise c.WinError(c.get_last_error())
            size = w.DWORD()
            api.SetupDiGetDeviceInterfaceDetailW(handle, c.byref(data), None, 0, c.byref(size), None)
            buf = c.create_string_buffer(size.value)
            c.cast(buf, c.POINTER(w.DWORD))[0] = 8 if c.sizeof(c.c_void_p) == 8 else 6
            if not api.SetupDiGetDeviceInterfaceDetailW(handle, c.byref(data), buf, size.value, None, None):
                raise c.WinError(c.get_last_error())
            path = c.wstring_at(c.addressof(buf) + 4)
            if 'vid_0483&pid_f123' in path.lower():
                paths.append(path)
    finally:
        api.SetupDiDestroyDeviceInfoList(handle)
    return paths


class PhytonTransport:
    def __init__(self, serial_number=None):
        from radiacode.bytes_buffer import BytesBuffer
        self.BytesBuffer = BytesBuffer
        paths = device_paths()
        if serial_number:
            paths = [p for p in paths if ('#' + serial_number.lower() + '#') in p.lower()]
        if len(paths) != 1:
            raise OSError(f'Expected one Radiacode USB device, found {len(paths)}. Check serial, cable, and device power.')
        self.path = paths[0]
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.k.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, c.c_void_p, w.DWORD, w.DWORD, w.HANDLE]
        self.k.CreateFileW.restype = w.HANDLE
        self.k.CreateEventW.argtypes = [c.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
        self.k.CreateEventW.restype = w.HANDLE
        self.k.CloseHandle.argtypes = [w.HANDLE]
        self.k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        for name in ['ReadFile', 'WriteFile']:
            getattr(self.k, name).argtypes = [w.HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.POINTER(Overlapped)]
        self.k.CancelIoEx.argtypes = [w.HANDLE, c.POINTER(Overlapped)]
        self.k.GetOverlappedResult.argtypes = [w.HANDLE, c.POINTER(Overlapped), c.POINTER(w.DWORD), w.BOOL]
        self.handle = self.k.CreateFileW(self.path, 0xC0000000, 0, None, 3, 0x40000000, None)
        if self.handle == c.c_void_p(-1).value:
            self.handle = None
            raise c.WinError(c.get_last_error())
        # Clear stale command responses only. DATA_BUF is read separately and archived.
        try:
            for _ in range(16):
                self._io(False, 256, 100)
        except TimeoutError:
            pass
        except BaseException:
            self.close()
            raise

    def _io(self, writing, value, timeout_ms):
        if self.handle is None:
            raise OSError('USB handle closed')
        buf = c.create_string_buffer(value) if writing else c.create_string_buffer(value)
        size = len(value) if writing else value
        event = self.k.CreateEventW(None, True, False, None)
        if not event:
            raise c.WinError(c.get_last_error())
        ov = Overlapped(hEvent=event)
        n = w.DWORD()
        try:
            fn = self.k.WriteFile if writing else self.k.ReadFile
            if not fn(self.handle, buf, size, c.byref(n), c.byref(ov)):
                err = c.get_last_error()
                if err == 121:
                    raise TimeoutError('Driver bulk I/O timed out')
                if err != 997:
                    raise c.WinError(err)
                wait = self.k.WaitForSingleObject(event, max(1, int(timeout_ms)))
                if wait == 258:
                    self.k.CancelIoEx(self.handle, c.byref(ov))
                    # Keep the OVERLAPPED and buffer alive until cancellation completes.
                    self.k.GetOverlappedResult(self.handle, c.byref(ov), c.byref(n), True)
                    raise TimeoutError('USB bulk I/O timed out')
                if wait != 0 or not self.k.GetOverlappedResult(self.handle, c.byref(ov), c.byref(n), False):
                    err = c.get_last_error()
                    if err == 121:
                        raise TimeoutError('Driver bulk I/O timed out')
                    raise c.WinError(err)
            if writing:
                if n.value != size:
                    raise OSError(f'Short USB write {n.value}/{size}')
                return n.value
            return buf.raw[:n.value]
        finally:
            self.k.CloseHandle(event)

    def execute(self, request):
        deadline = time.monotonic() + 4
        self._io(True, request, 3000)
        data = self._io(False, 256, 3000)
        if len(data) < 4:
            raise OSError('Truncated USB length header')
        size = struct.unpack_from('<I', data)[0]
        if not 4 <= size <= 4_194_304:
            raise OSError(f'Invalid USB response length {size}')
        data = data[4:]
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('USB response deadline exceeded')
            chunk = self._io(False, size - len(data), remaining * 1000)
            if not chunk:
                raise OSError('Empty USB response chunk')
            data += chunk
        if len(data) != size:
            raise OSError('USB response length mismatch')
        return self.BytesBuffer(data)

    def close(self):
        if self.handle is not None:
            self.k.CloseHandle(self.handle)
            self.handle = None
