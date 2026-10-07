import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bootstrap
import copy
import datetime as dt
import json
import multiprocessing as mp
import tempfile
import time
import unittest

import numpy as np
from analysis import reconstruct, retry_delay, weighted_telemetry
from storage import RunStore, immutable_json
from recorder import WorkerClient, DeviceFailure
from clock_check import ntp_stamp, parse_reply
from device import ReadoutDevice, UTCClock
from radiacode.types import COMMAND, VSFR
import struct
from export_run import export_run


def snapshot(second, counts, duration=None, **changes):
    utc = (dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=second)).isoformat()
    value = dict(serial='TEST', boot_id='boot', connection_id='connection', spectrum_generation=0,
                 had_disconnect=False, utc_before=utc, utc_after=utc,
                 monotonic_before=float(second), monotonic_after=float(second), read_time_halfwidth_s=0.,
                 spectrum=dict(counts=list(counts), duration_s=second + 100 if duration is None else duration,
                               calibration_keV=[5., 2.4, .0003], raw_hex='test', format_version=0))
    value.update(changes)
    return value


def blocked_worker(pipe):
    time.sleep(30)


class ScientificRules(unittest.TestCase):
    def test_existing_counts_are_baseline_not_first_bin(self):
        old, new = snapshot(0, [10000, 20000]), snapshot(10, [10003, 20007])
        self.assertIsNone(reconstruct(None, old, 10)['counts_delta'])
        result = reconstruct(old, new, 10)
        self.assertEqual(result['counts_delta'], [3, 7])
        self.assertEqual(result['count_rate_cps'], 1.)

    def test_actual_duration_not_nominal_interval(self):
        result = reconstruct(snapshot(0, [500], 100), snapshot(10, [533], 111), 10)
        self.assertEqual(result['count_rate_cps'], 3.)
        self.assertEqual(result['poisson_sigma_cps'], np.sqrt(33) / 11)

    def test_unsigned_underflow_is_reset_not_clipped(self):
        result = reconstruct(snapshot(0, [0xFFFFFFFE]), snapshot(10, [1]), 10)
        self.assertIsNone(result['counts_delta'])
        self.assertIn('count_counter_decreased', result['quality'])
        self.assertEqual(result['epoch'], 1)

    def test_calibration_change_creates_boundary(self):
        new = snapshot(10, [20])
        new['spectrum']['calibration_keV'][0] += .1
        self.assertIsNone(reconstruct(snapshot(0, [10]), new, 10)['counts_delta'])

    def test_reset_event_even_if_counts_exceed_old_counts(self):
        result = reconstruct(snapshot(0, [10]), snapshot(10, [100], spectrum_generation=1), 10)
        self.assertIsNone(result['counts_delta'])

    def test_reconnect_preserves_aggregate_but_flags_missing_bins(self):
        result = reconstruct(snapshot(0, [100]), snapshot(40, [200], connection_id='new', had_disconnect=True), 10)
        self.assertEqual(result['total_counts'], 100)
        self.assertIn('gap_recovered_aggregate', result['quality'])
        self.assertIn('reconnect_aggregate_continuity_inferred', result['quality'])

    def test_clock_step_does_not_change_device_count_rate(self):
        new = snapshot(30, [120])
        new['spectrum']['duration_s'] = 110
        new['monotonic_before'] = new['monotonic_after'] = 10
        result = reconstruct(snapshot(0, [100]), new, 10)
        self.assertEqual(result['count_rate_cps'], 2.)
        self.assertIn('host_clock_step', result['quality'])

    def test_seven_days_no_rolling_limit_and_count_conservation(self):
        old = snapshot(0, [10000, 50000, 0, 40])
        first = old['spectrum']['counts'].copy()
        total = [0] * 4
        # 60,480 exact 10s intervals; simulated numerical coverage, not hardware soak.
        for index in range(1, 60481):
            increments = [index % 3, index % 5, 2, 1]
            new = snapshot(index * 10, [a + b for a, b in zip(old['spectrum']['counts'], increments)])
            result = reconstruct(old, new, 10)
            total = [a + b for a, b in zip(total, result['counts_delta'])]
            self.assertEqual(result['quality'], ['normal'])
            old = new
        self.assertEqual(total, [a - b for a, b in zip(old['spectrum']['counts'], first)])

    def test_telemetry_gap_is_not_interpolated(self):
        base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        rows = [dict(type='RealTimeData', dt=dt.datetime.fromtimestamp(base + n, dt.timezone.utc).isoformat(),
                     count_rate=10., dose_rate=1e-5) for n in (0, 1, 2, 9)]
        result = weighted_telemetry(rows, base, base + 10)
        self.assertAlmostEqual(result['coverage_fraction'], .6)
        self.assertIsNone(result['integrated_dose_uSv'])
        self.assertAlmostEqual(result['dose_rate_uSv_h'], .1)

    def test_exponential_schedule_has_cap(self):
        self.assertEqual([retry_delay(i) for i in range(9)], [1, 2, 4, 8, 16, 32, 60, 60, 60])
        self.assertEqual(retry_delay(100000), 60)

    def test_duplicate_telemetry_timestamp_does_not_double_weight(self):
        base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc).timestamp()
        rows = [dict(type='RealTimeData', dt=dt.datetime.fromtimestamp(base + n, dt.timezone.utc).isoformat(),
                     count_rate=10., dose_rate=1e-5) for n in [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]]
        result = weighted_telemetry(rows, base, base + 10)
        self.assertEqual(result['coverage_fraction'], 1.)
        self.assertAlmostEqual(result['observed_integrated_dose_uSv'], .1 * 10 / 3600)


class Durability(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / 'validation' / 'test_temp'
        self.test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_root)
        self.path = Path(self.temp.name) / 'run'
        self.config = dict(interval_s=10, mode='demo', serial='TEST')

    def tearDown(self):
        assert Path(self.temp.name).resolve().parent == self.test_root.resolve()
        self.temp.cleanup()

    def test_atomic_raw_commit_recovers_missing_index(self):
        store = RunStore(self.path, self.config)
        store.append_snapshot(snapshot(0, [100]))
        orphan = dict(snapshot(10, [120]), seq=1, schema=1)
        immutable_json(self.path / 'spectra' / '2026-01-01' / '000000001.json', orphan)
        store.close()
        recovered = RunStore(self.path)
        try:
            self.assertEqual(recovered.next_seq, 2)
            d = json.loads(recovered.db.execute('SELECT derived FROM snapshots WHERE seq=1').fetchone()[0])
            self.assertEqual(d['total_counts'], 20)
        finally:
            recovered.close()

    def test_integrity_change_blocks_resume(self):
        store = RunStore(self.path, self.config)
        store.append_snapshot(snapshot(0, [100]))
        store.close()
        raw = next((self.path / 'spectra').rglob('*.json'))
        value = json.loads(raw.read_text())
        value['spectrum']['counts'][0] += 1
        raw.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            RunStore(self.path)

    def test_missing_raw_file_blocks_resume(self):
        store = RunStore(self.path, self.config)
        store.append_snapshot(snapshot(0, [100]))
        store.close()
        next((self.path / 'spectra').rglob('*.json')).unlink()
        with self.assertRaisesRegex(ValueError, 'missing raw'):
            RunStore(self.path)

    def test_original_file_never_overwritten(self):
        file = Path(self.temp.name) / 'original.json'
        immutable_json(file, dict(original=True))
        before = file.read_bytes()
        with self.assertRaises(FileExistsError):
            immutable_json(file, dict(original=False))
        self.assertEqual(file.read_bytes(), before)

    def test_second_writer_blocked(self):
        first = RunStore(self.path, self.config)
        try:
            with self.assertRaises(RuntimeError):
                RunStore(self.path)
        finally:
            first.close()

    def test_utc_day_rollover_and_export_counts(self):
        store = RunStore(self.path, self.config)
        store.append_snapshot(snapshot(86390, [100, 100]))
        store.append_snapshot(snapshot(86400, [110, 120]))
        store.append_snapshot(snapshot(86410, [111, 124]))
        store.close()
        self.assertEqual(len(list((self.path / 'spectra').iterdir())), 2)
        target, report = export_run(self.path)
        self.assertEqual(report['nominal_counts'], 35)
        with np.load(target / 'spectra_00000.npz', allow_pickle=False) as pack:
            self.assertEqual(pack['valid'].tolist(), [False, True, True])
            self.assertEqual(pack['counts_delta'][1:].sum(axis=0).tolist(), [11, 24])

    def test_recovery_uses_sequence_when_utc_moves_back_across_midnight(self):
        store = RunStore(self.path, self.config)
        store.append_snapshot(snapshot(86401, [100], 100))
        changed_clock = snapshot(86399, [110], 110)
        changed_clock['monotonic_before'] = changed_clock['monotonic_after'] = 86411
        store.append_snapshot(changed_clock)
        store.close()
        recovered = RunStore(self.path)
        try:
            self.assertEqual(recovered.next_seq, 2)
            self.assertEqual(recovered.previous['spectrum']['counts'], [110])
        finally:
            recovered.close()


class BoundedIOAndTime(unittest.TestCase):
    def test_measurement_and_calibration_writes_are_blocked(self):
        device = ReadoutDevice.__new__(ReadoutDevice)
        # These must fail before touching a USB handle or sequence state.
        for command, payload in [(COMMAND.WR_VIRT_STRING, b''),
                                 (COMMAND.WR_VIRT_SFR, struct.pack('<I', int(VSFR.DOSE_RESET))),
                                 (COMMAND.WR_VIRT_SFR_BATCH, b'')]:
            with self.assertRaises(PermissionError):
                device.execute(command, payload)

    def test_utc_reference_uses_monotonic_clock(self):
        from unittest.mock import patch
        with patch('device.time.time', return_value=1000.), patch('device.time.perf_counter', return_value=10.):
            clock = UTCClock(dict(offset_s=.1, verified=True))
        with patch('device.time.time', return_value=5000.), patch('device.time.perf_counter', return_value=15.):
            self.assertAlmostEqual(clock.seconds(), 1005.1)

    def test_hung_worker_deadline_and_cleanup(self):
        ctx = mp.get_context('spawn')
        client = WorkerClient.__new__(WorkerClient)
        client.pipe, child = ctx.Pipe()
        client.process = ctx.Process(target=blocked_worker, args=(child,), daemon=True)
        client.process.start()
        child.close()
        try:
            with self.assertRaises(DeviceFailure):
                client.receive(.15)
        finally:
            client.close()
        self.assertFalse(client.process.is_alive())

    def test_sntp_offset_and_invalid_origin(self):
        t1, t2, t3, t4 = 1000., 1000.11, 1000.12, 1000.03
        stamp = ntp_stamp(t1)
        reply = bytearray(48)
        reply[0], reply[1] = 0x24, 2
        reply[24:32], reply[32:40], reply[40:48] = stamp, ntp_stamp(t2), ntp_stamp(t3)
        result = parse_reply(reply, stamp, t1, t4)
        self.assertAlmostEqual(result['offset_s'], .1, places=5)
        self.assertAlmostEqual(result['round_trip_s'], .02, places=5)
        with self.assertRaises(ValueError):
            parse_reply(reply, b'wrongorg', t1, t4)


if __name__ == '__main__':
    mp.freeze_support()
    unittest.main(verbosity=2)
