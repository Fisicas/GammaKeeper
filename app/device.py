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
from telemetry_decoder import decode_buffer
from system_time import boot_identity


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='microseconds')


class UTCClock:
    SLEW_RATE = .005  # At most 5 ms correction/second; time remains increasing.
    def __init__(self, measurement):
        self.anchor_mono = time.perf_counter()
        try:
            self.anchor_utc = self.reference_seconds(measurement, self.anchor_mono)
        except (ValueError, KeyError, TypeError) as error:
            measurement = dict(verified=False,offset_s=0.,note=f'Invalid startup time reference: {error}')
            self.anchor_utc = time.time()
        self.measurement = measurement
        self.pending = 0.
        self.last_good = measurement.get('reference_monotonic_s',self.anchor_mono) if measurement.get('verified') else None
        self.last_failure = None
        held=measurement.get('previous_clock')
        if not measurement.get('verified') and held and held.get('system_boot_id') is not None and held['system_boot_id']==boot_identity():
            elapsed=self.anchor_mono-held.get('anchor_monotonic_s',float('inf'))
            if elapsed>=0 and held.get('last_good_monotonic_s') is not None:
                old_pending=held.get('slew_total_s',0.)
                applied=max(-elapsed*self.SLEW_RATE,min(old_pending,elapsed*self.SLEW_RATE))
                self.anchor_utc=held['anchor_utc_s']+elapsed+applied
                self.pending=old_pending-applied
                self.last_good=held['last_good_monotonic_s']
                self.measurement={key:held.get(key) for key in ('offset_s','server','source_count','checked_host_utc')}
                self.measurement.update(estimated_uncertainty_s=held.get('base_uncertainty_s'),
                                        note='Preserved monotonic UTC reference across recorder restart; network check failed')
                self.last_failure={key:value for key,value in measurement.items() if key!='previous_clock'}

    @staticmethod
    def reference_seconds(measurement, now):
        if measurement.get('verified') and measurement.get('reference_monotonic_s') is not None:
            age = now - measurement['reference_monotonic_s']
            if not 0 <= age <= 120:
                raise ValueError('UTC measurement has an invalid/stale monotonic reference')
            return measurement['reference_unix_s'] + age
        return time.time() + (measurement.get('offset_s', 0.) if measurement.get('verified') else 0.)

    def seconds(self):
        elapsed = time.perf_counter() - self.anchor_mono
        correction = max(-elapsed*self.SLEW_RATE, min(self.pending, elapsed*self.SLEW_RATE))
        return self.anchor_utc + elapsed + correction

    def now(self):
        return dt.datetime.fromtimestamp(self.seconds(), dt.timezone.utc)

    def update(self, measurement):
        now = time.perf_counter()
        current = self.seconds()
        if not measurement.get('verified'):
            self.last_failure = {key:value for key,value in measurement.items() if key!='previous_clock'}
            return 0.
        corrected = self.reference_seconds(measurement, now)
        if abs(corrected-current) > 2.:
            self.last_failure = dict(measurement, verified=False, note='UTC reference jump >2s rejected during capture')
            return 0.
        self.anchor_mono = now
        self.anchor_utc = current
        self.pending = corrected-current
        self.measurement = measurement
        self.last_good = measurement.get('reference_monotonic_s',now)
        self.last_failure = None
        return 0.  # No instantaneous shift of UTC or original device timestamps.

    def metadata(self):
        value = {k: self.measurement.get(k) for k in ('offset_s','server','source_count','checked_host_utc','note')}
        now = time.perf_counter()
        age = now-self.last_good if self.last_good is not None else None
        elapsed = now-self.anchor_mono
        residual = self.pending-max(-elapsed*self.SLEW_RATE,min(self.pending,elapsed*self.SLEW_RATE))
        quality = ('unverified' if age is None else 'holdover' if self.last_failure or age>1800
                   else 'slewing' if abs(residual)>.001 else 'single_source' if self.measurement.get('source_count',2)<2 else 'referenced')
        base_error = self.measurement.get('estimated_uncertainty_s')
        value.update(verified=age is not None and quality!='holdover', quality=quality,
                     reference_age_s=age, slew_remaining_s=residual,
                     estimated_uncertainty_s=(base_error + abs(residual) + age*100e-6) if base_error is not None and age is not None else None,
                     holdover_growth_assumption_ppm=100.,
                     uncertainty_note='Engineering estimate; 100 ppm holdover growth is assumed, not a measured/certified oscillator bound',
                     last_check_failure=self.last_failure,
                     anchor_utc_s=self.anchor_utc, anchor_monotonic_s=self.anchor_mono,
                     slew_total_s=self.pending,system_boot_id=boot_identity())
        value.update(last_good_monotonic_s=self.last_good,base_uncertainty_s=self.measurement.get('estimated_uncertainty_s'))
        return value


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


# Click-sound flag in SOUND_CTRL, as written by the radiacode library's set_sound_ctrl (CTRL.CLICKS).
SOUND_CLICKS = 0x2


class ReadoutDevice(RadiaCode):
    def __init__(self, serial, clock=None):
        self._permitted_write = None  # the single register write currently allowed (see set_click_sound)
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
            if args != struct.pack('<II', int(VSFR.DEVICE_TIME), 0) and (self._permitted_write is None or args != self._permitted_write):
                raise PermissionError('Only exchange timebase initialization and the click-sound flag may be written')
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

    def _read_register(self, register):
        """Raw 32-bit register value (no library format decoding)."""
        response = self.execute(COMMAND.RD_VIRT_SFR_BATCH, struct.pack('<II', 1, int(register)))
        valid, value = response.unpack('<II')
        if valid != 1:
            raise OSError(f'Register {int(register):#06x} could not be read')
        return value

    def _write_register(self, register, value):
        self._permitted_write = struct.pack('<II', int(register), value)
        try:
            response = self.execute(COMMAND.WR_VIRT_SFR, self._permitted_write)
        finally:
            self._permitted_write = None
        code = response.unpack('<I')[0]
        if code != 1:
            raise OSError(f'Register {int(register):#06x} write refused (status {code})')

    def set_click_sound(self, on):
        """Turn only the per-particle click sound on or off.

        Read-modify-write of the raw SOUND_CTRL value: every other bit (alarm and button sounds)
        is kept. The value is read back; if the device did not keep it, the original is written
        back exactly and an error is reported. No measurement setting is touched.
        """
        before = self._read_register(VSFR.SOUND_CTRL)
        target = before | SOUND_CLICKS if on else before & ~SOUND_CLICKS
        result = dict(requested_clicks=bool(on), sound_ctrl_before=before, sound_ctrl_target=target)
        after = before
        if target != before:
            self._write_register(VSFR.SOUND_CTRL, target)
            after = self._read_register(VSFR.SOUND_CTRL)
            if after != target:
                self._write_register(VSFR.SOUND_CTRL, before)
                result.update(sound_ctrl_after=self._read_register(VSFR.SOUND_CTRL),
                              error='The device did not keep the click-sound setting; its original value was restored')
                return result
        result.update(sound_ctrl_after=after, clicks=bool(after & SOUND_CLICKS),
                      sound_on_raw=self._read_register(VSFR.SOUND_ON))
        return result

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
        return decode_buffer(raw,self._base_time,self.configuration_text)

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
                result = dict(clock_shift_s=shift, clock=device.clock.metadata())
                command = 'clock'
            elif isinstance(command, dict) and command.get('kind') == 'click_sound':
                try:  # report failures instead of ending the worker (and the connection)
                    result = device.set_click_sound(bool(command.get('on')))
                except (OSError, ValueError, PermissionError) as error:
                    result = dict(requested_clicks=bool(command.get('on')), error=f'{type(error).__name__}: {error}')
                command = 'click_sound'
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
            import traceback
            connection.send(dict(kind='error', error=f'{type(error).__name__}: {error}',
                                 error_type=type(error).__name__,winerror=getattr(error,'winerror',None),
                                 traceback=traceback.format_exc()))
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        if device:
            device.close()
        connection.close()
