"""Restricted device client; readout and exchange-clock initialization only."""
import bootstrap
import datetime as dt
import math
import struct
import time
import weakref
from dataclasses import asdict
from enum import Enum

from radiacode.radiacode import RadiaCode
from radiacode.types import COMMAND, VS, VSFR
from radiacode.bytes_buffer import BytesBuffer
from radiacode.decoders.databuf import decode_VS_DATA_BUF
from radiacode.decoders.spectrum import decode_RC_VS_SPECTRUM
from phyton import PhytonTransport


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')


class UTCClock:
    def __init__(self, measurement):
        self.anchor_mono = time.perf_counter()
        self.anchor_utc = time.time() + measurement.get('offset_s', 0.)
        self.measurement = measurement

    def seconds(self):
        return self.anchor_utc + time.perf_counter() - self.anchor_mono

    def now(self):
        return dt.datetime.fromtimestamp(self.seconds(), dt.timezone.utc)

    def update(self, measurement):
        corrected = time.time() + measurement['offset_s']
        shift = corrected - self.seconds()
        self.anchor_mono = time.perf_counter()
        self.anchor_utc = corrected
        self.measurement = measurement
        return shift

    def metadata(self):
        return {k: self.measurement.get(k) for k in ('offset_s', 'verified', 'server', 'estimated_uncertainty_s', 'checked_host_utc', 'note')}


def clean(value):
    if isinstance(value, dt.datetime):
        return value.isoformat(timespec='microseconds')
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Nonfinite device value')
    return value


class ReadoutDevice(RadiaCode):
    def __init__(self, serial, clock=None):
        self.clock = UTCClock(clock or dict(offset_s=0., verified=False))
        self._seq = 0
        self._connection = PhytonTransport(serial)
        self._finalizer = weakref.finalize(self, self._connection.close)
        try:
            self.execute(COMMAND.SET_EXCHANGE, b'\x01\xff\x12\xff')
            self.firmware = clean(self.fw_version())
            if tuple(self.firmware[1][:2]) < (4, 8):
                raise RuntimeError('This decoder requires firmware >=4.8')
            self.serial = self.serial_number().strip('\x00')
            if serial and self.serial != serial:
                raise RuntimeError(f'Serial mismatch: requested {serial}, got {self.serial}')
            self.configuration_text = self.configuration()
            self._spectrum_format_version = 0
            for line in self.configuration_text.splitlines():
                if line.startswith('SpecFormatVersion='):
                    self._spectrum_format_version = int(line.split('=', 1)[1])
            # Preserve pre-anchor backlog before resetting the exchange timestamp.
            # Its timestamps belong to the previous client; do not relabel them.
            self.pre_anchor_buffer_hex = self.read_request(VS.DATA_BUF).data().hex()
            start = utc_now()
            mono_start = time.monotonic()
            self.set_local_time(self.clock.now().astimezone().replace(tzinfo=None))
            self.device_time(0)
            self._base_time = self.clock.now() + dt.timedelta(seconds=128)
            self.anchor = dict(utc_start=start, utc_end=utc_now(), monotonic_start=mono_start,
                               monotonic_end=time.monotonic(), base_utc=self._base_time.isoformat(),
                               clock=self.clock.metadata(),
                               writes=['SET_EXCHANGE', 'SET_TIME (corrected local civil time)', 'DEVICE_TIME=0 (exchange timebase)'])
        except BaseException:
            self.close()
            raise

    def execute(self, reqtype, args=None):
        allowed = {COMMAND.GET_STATUS, COMMAND.GET_VERSION, COMMAND.GET_SERIAL, COMMAND.FW_SIGNATURE,
                   COMMAND.RD_VIRT_STRING, COMMAND.RD_VIRT_SFR, COMMAND.RD_VIRT_SFR_BATCH,
                   COMMAND.SET_TIME, COMMAND.SET_EXCHANGE}
        if reqtype == COMMAND.WR_VIRT_SFR:
            if args != struct.pack('<II', int(VSFR.DEVICE_TIME), 0):
                raise PermissionError('Only exchange timebase initialization is permitted')
        elif reqtype not in allowed:
            raise PermissionError(f'Blocked device command {reqtype}')
        # Replace upstream assertion (removed under python -O) with a real check.
        seq = 0x80 + self._seq
        self._seq = (self._seq + 1) % 32
        header = struct.pack('<HBB', int(reqtype), 0, seq)
        request = header + (args or b'')
        response = self._connection.execute(struct.pack('<I', len(request)) + request)
        if response.unpack('<4s')[0] != header:
            raise OSError('USB response sequence/header mismatch')
        return response

    def read_request(self, command_id):
        r = self.execute(COMMAND.RD_VIRT_STRING, struct.pack('<I', int(command_id)))
        code, length = r.unpack('<II')
        if code != 1:
            raise OSError(f'Device read {command_id}: status {code}')
        if r.size() == length + 1 and r._data[-1] == 0:
            r._data = r._data[:-1]
        if r.size() != length:
            raise OSError(f'Device read {command_id}: length mismatch')
        return r

    def info(self):
        return dict(serial=self.serial, firmware=self.firmware, transport='Phyton overlapped bulk I/O',
                    spectrum_format=self._spectrum_format_version, configuration=self.configuration_text,
                    exchange_clock_anchor=self.anchor, pre_anchor_buffer_hex=self.pre_anchor_buffer_hex,
                    pre_anchor_timestamp_mapping='unknown; prior client anchor not available')

    def read_buffer(self):
        raw = self.read_request(VS.DATA_BUF).data()
        buffer = BytesBuffer(raw)
        warning = None
        try:
            decoded = decode_VS_DATA_BUF(buffer, self._base_time, ignore_errors=False)
            records = [dict(type=type(rec).__name__, **clean(asdict(rec))) for rec in decoded]
            if buffer.size():
                warning = f'{buffer.size()} unparsed bytes; complete raw payload retained'
        except Exception as error:
            records = []
            warning = f'Decode failed: {type(error).__name__}: {error}; complete raw payload retained'
        return dict(raw_hex=raw.hex(), records=records, decode_warning=warning)

    def read_spectrum(self):
        raw = self.read_request(VS.SPECTRUM).data()
        spectrum = decode_RC_VS_SPECTRUM(BytesBuffer(raw), self._spectrum_format_version)
        counts = spectrum.counts
        if not counts or any(type(v) is not int or v < 0 or v > 0xFFFFFFFF for v in counts):
            raise ValueError('Invalid unsigned spectrum counts')
        calib = [spectrum.a0, spectrum.a1, spectrum.a2]
        if not all(math.isfinite(v) for v in calib):
            raise ValueError('Invalid energy calibration')
        return dict(duration_s=spectrum.duration.total_seconds(), calibration_keV=calib,
                    counts=counts, raw_hex=raw.hex(), format_version=self._spectrum_format_version)


def io_worker(connection, serial, clock=None):
    """One device owner. Parent enforces a hard deadline and can terminate this process."""
    device = None
    try:
        device = ReadoutDevice(serial, clock)
        connection.send(dict(kind='ready', info=device.info()))
        while True:
            command = connection.recv()
            if command == 'close':
                break
            before = device.clock.now().isoformat(timespec='microseconds')
            host_before = utc_now()
            m0 = time.monotonic()
            if isinstance(command, dict) and command.get('kind') == 'clock':
                shift = device.clock.update(command['measurement'])
                device._base_time += dt.timedelta(seconds=shift)
                result = dict(clock_shift_s=shift)
                command = 'clock'
            elif command == 'buffer':
                result = device.read_buffer()
            elif command == 'spectrum':
                result = device.read_spectrum()
            else:
                raise ValueError(f'Unknown worker command {command}')
            connection.send(dict(kind=command, result=result, utc_before=before,
                                 utc_after=device.clock.now().isoformat(timespec='microseconds'),
                                 host_utc_before=host_before, host_utc_after=utc_now(), clock=device.clock.metadata(),
                                 monotonic_before=m0, monotonic_after=time.monotonic()))
    except BaseException as error:
        try:
            connection.send(dict(kind='error', error=f'{type(error).__name__}: {error}'))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if device:
            device.close()
        connection.close()
