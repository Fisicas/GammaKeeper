"""Independent recorder process; GUI is a detachable monitor/controller."""
import bootstrap
import argparse
import datetime as dt
import importlib.metadata
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import traceback
import uuid

from analysis import VERSION, midpoint, timestamp, retry_delay, weighted_telemetry, DOSE_R_TO_USV
from device import io_worker, utc_now
from storage import RunStore, DeviceMutex, immutable_json, canonical
from status_cache import StatusPublisher
from worker_client import WorkerClient, DeviceFailure
from runtime import python_command
import sqlite3
from status_observations import select_rare
from telemetry_analysis import observation_from_batch


class DemoClient:
    """Clearly labeled synthetic input. Does not import/open a device transport."""
    def __init__(self, state=None):
        import numpy as np
        self.rng = np.random.default_rng(417)
        self.start = time.monotonic()
        self.counts = list(state['spectrum']['counts']) if state else [0] * 1024
        self.duration = state['spectrum']['duration_s'] if state else 0
        self.last = time.monotonic()
        self.info = dict(serial='SIMULATED', firmware='SIMULATED', transport='Synthetic Poisson input')

    def request(self, command, timeout=12):
        if isinstance(command, dict):
            return dict(result=dict(clock_shift_s=0.))
        now = time.monotonic()
        elapsed = now - self.last
        if command == 'spectrum':
            import numpy as np
            channels = np.arange(1024)
            weights = np.exp(-channels / 100) + .6 * np.exp(-.5 * ((channels - 270) / 9)**2)
            increments = self.rng.poisson(weights / weights.sum() * 80 * elapsed)
            self.counts = [a + int(b) for a, b in zip(self.counts, increments)]
            self.duration += elapsed
            self.last = now
            result = dict(duration_s=self.duration, calibration_keV=[5., 2.43, .00037],
                          counts=self.counts.copy(), raw_hex=None, format_version='SIMULATED')
        else:
            result = dict(raw_hex=None, decode_warning=None, records=[dict(type='RealTimeData', dt=utc_now(),
                          count_rate=80., dose_rate=1.2e-5, count_rate_err=3., dose_rate_err=5.,
                          flags=0, real_time_flags=0), dict(type='RareData', dt=utc_now(),
                          dose=1e-5 + (now - self.start) * 1.2e-5 / 3600,
                          duration=now - self.start, temperature=22., charge_level=100., flags=0)])
        return dict(kind=command, result=result, utc_before=utc_now(), utc_after=utc_now(),
                    monotonic_before=now, monotonic_after=now)

    def close(self):
        pass


def clock_audit(results):
    measurement = clock_measurement()
    try:
        p = subprocess.run(['w32tm', '/query', '/status', '/verbose'], capture_output=True,
                           timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        value = dict(returncode=p.returncode, stdout=p.stdout.decode('utf-8', 'replace'),
                     stderr=p.stderr.decode('utf-8', 'replace'), note='Read-only Windows Time query; not an independent UTC accuracy measurement')
    except Exception as error:
        value = dict(error=f'{type(error).__name__}: {error}')
    value['sntp'] = measurement
    results.put(value)


def clock_measurement():
    # Bound DNS/network hangs in a child process, independently of acquisition.
    try:
        p = subprocess.run([python_command(), '-X', 'utf8', str(Path(__file__).with_name('clock_check.py'))],
                           capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        if p.returncode:
            raise RuntimeError(p.stderr.decode('utf-8', 'replace'))
        return json.loads(p.stdout.decode('utf-8'))
    except Exception as error:
        return dict(offset_s=0., verified=False, estimated_uncertainty_s=None,
                    note=f'Clock query failed; host UTC used: {type(error).__name__}: {error}')


def versions():
    import platform
    out = {'python': platform.python_version(), 'platform': platform.platform(), 'app': VERSION}
    out.update(python_executable=sys.executable,python_prefix=sys.prefix)
    for name in ['radiacode', 'PySide6', 'numpy', 'matplotlib', 'pyusb', 'libusb-package']:
        try:
            out[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = 'not installed'
    return out


def record(path, config=None, test_reconnect_at=None, stop_request=None):
    store = None
    client = None
    mutex = None
    boot = uuid.uuid4().hex
    connection_id = None
    mode = config.get('mode') if config else json.loads((Path(path) / 'manifest.json').read_text())['mode']
    serial = config.get('serial') if config else json.loads((Path(path) / 'manifest.json').read_text())['serial']
    state = dict(state='starting', run_path=str(Path(path).resolve()), boot_id=boot, pid=os.getpid(), mode=mode,
                 serial=serial, software_version=VERSION, utc=utc_now(), retries=0)
    records = []
    last_rt = None
    last_rare = None
    failure_index = 0
    gap = False
    successful_spectra = 0
    generation = 0
    results = queue.Queue()
    start = time.monotonic()
    next_audit = start
    next_status = start
    tested_reconnect = False
    outcome = 'stopped'
    owns_status = False
    dose_baseline = None
    dose_reset_seen = False
    clock = dict(offset_s=0., verified=False)
    dose_integral = 0.
    dose_observed_s = 0.
    dose_elapsed_s = 0.
    publisher = None
    last_ui_persist = -float('inf')
    last_ui_state = None
    exit_code = 0
    def publish(value, force=False):
        nonlocal last_ui_persist,last_ui_state
        if publisher:
            publisher.submit(value)
        # Authoritative compact fallback is independent of replaceable JSON.
        # Optional UI persistence never turns a cache problem into acquisition failure.
        if store and getattr(store,'db',None) and (force or value.get('state') != last_ui_state or time.monotonic()-last_ui_persist>=10):
            compact = {k:v for k,v in value.items() if k not in ('last_snapshot','last_interval')}
            compact['last_snapshot_seq'] = store.next_seq-1 if store.next_seq else None
            try:
                store.db.execute('PRAGMA busy_timeout=0')
                store.set_state('ui_status',compact)
                last_ui_persist,last_ui_state = time.monotonic(),value.get('state')
            except (sqlite3.Error,OSError) as error:
                print(f'Optional UI state persistence failed: {error}',file=sys.stderr,flush=True)
            finally:
                try:
                    store.db.execute('PRAGMA busy_timeout=5000')
                except sqlite3.Error:
                    pass
    try:
        mutex = DeviceMutex(serial)
        store = RunStore(path, config)
        owns_status = True
        publisher = StatusPublisher(path)
        config = store.config
        state['interval_s'] = config['interval_s']
        state['planned_end_utc'] = config.get('planned_end_utc')
        state['snapshots'] = store.next_seq
        if stop_request:
            state['stop_request'] = str(Path(stop_request).resolve())
        if store.previous:
            generation = store.previous['spectrum_generation']
            gap = True
        store.event('recorder_started', dict(boot_id=boot, versions=versions(), resumed=bool(store.previous)))
        store.set_state('recorder', dict(boot_id=boot, pid=os.getpid(), status='running'))
        old_dose = store.db.execute("SELECT value FROM state WHERE key='dose_baseline'").fetchone()
        dose_baseline = json.loads(old_dose[0]) if old_dose else None
        old_reset = store.db.execute("SELECT value FROM state WHERE key='dose_reset_seen'").fetchone()
        dose_reset_seen = bool(json.loads(old_reset[0])) if old_reset else False
        # Recover derived totals from immutable snapshots, including a crash before
        # committing a separate state cache. No extrapolation across an outage.
        for name, in store.db.execute('SELECT file FROM snapshots ORDER BY seq'):
            old = json.loads((store.path / name).read_text())
            bin_data = old.get('telemetry_bin') or {}
            dose_integral += bin_data.get('observed_integrated_dose_uSv', bin_data.get('integrated_dose_uSv') or 0.)
            dose_observed_s += bin_data.get('observed_duration_s', 0.)
            if old.get('derived_observed_dose_elapsed_s') is not None:
                dose_elapsed_s = old['derived_observed_dose_elapsed_s']
        publish( dict(state, utc=utc_now(), message='Checking UTC reference'))
        if mode == 'device':
            clock = clock_measurement()
            store.event('clock_measurement', clock)
            state['clock_measurement'] = clock
        next_audit = time.monotonic() + 3600
        if config.get('planned_end_utc'):
            remaining = timestamp(config['planned_end_utc']) - time.time()
            if remaining <= 0:
                raise RuntimeError('Planned run end has passed; open/export this run or start a new run')
            stop_deadline = time.monotonic() + remaining
        else:
            stop_deadline = float('inf')
        # Prevent automatic system sleep while this recorder is active. Restore on exit.
        import ctypes
        sleep_result = ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
        store.event('automatic_sleep_inhibition', dict(request_succeeded=bool(sleep_result)))
        retry_at = start
        connected_since = None
        next_spectrum = start
        next_buffer = start
        while True:
            now = time.monotonic()
            stop_file = Path(path) / f'stop_{boot}.request'
            if stop_file.exists() or (stop_request and Path(stop_request).exists()) or now >= stop_deadline:
                outcome = 'completed' if now >= stop_deadline else 'stopped'
                # A short final interval is explicit, never expanded into a nominal 10s bin.
                if client and store.previous and now - (store.previous['monotonic_after'] if store.previous['boot_id'] == boot else start) > .5:
                    try:
                        msg = client.request('spectrum', timeout=5)
                        snapshot = make_snapshot(msg, serial, boot, connection_id, generation, gap,
                                                 last_rt, last_rare, final=True, dose_baseline=dose_baseline,
                                                 dose_reset_seen=dose_reset_seen)
                        snapshot['telemetry_bin'] = weighted_telemetry(records, midpoint(store.previous), midpoint(snapshot))
                        snapshot['telemetry_bin']['timestamp_basis']='host_reception_utc'
                        tb = snapshot['telemetry_bin']
                        dose_integral += tb.get('observed_integrated_dose_uSv', 0.)
                        dose_observed_s += tb.get('observed_duration_s', 0.)
                        dose_elapsed_s += max(0., midpoint(snapshot) - midpoint(store.previous))
                        snapshot.update(derived_observed_dose_uSv=dose_integral,
                                        derived_observed_dose_coverage=dose_observed_s / dose_elapsed_s if dose_elapsed_s else 0.,
                                        derived_observed_dose_elapsed_s=dose_elapsed_s)
                        saved, derived = store.append_snapshot(snapshot)
                        state.update(last_snapshot=saved, last_interval=derived, snapshots=store.next_seq)
                    except DeviceFailure as error:
                        store.event('final_read_failed', dict(error=str(error)))
                break
            if now >= next_audit:
                threading.Thread(target=clock_audit, args=(results,), daemon=True).start()
                next_audit = now + 3600
            while publisher and not publisher.warnings.empty():
                warning = publisher.warnings.get_nowait()
                kind = warning.pop('kind')
                store.event(kind,warning)
                state['status_cache_warning'] = warning if kind == 'status_cache_unavailable' else None
            while not results.empty():
                audit = results.get_nowait()
                store.event('clock_audit', audit)
                state['clock_audit'] = audit
                if audit['sntp'].get('verified'):
                    clock = audit['sntp']
                    state['clock_measurement'] = clock
                    if client:
                        try:
                            update = client.request(dict(kind='clock', measurement=clock))
                            store.event('utc_reference_updated', update['result'])
                        except DeviceFailure as error:
                            client.close()
                            client = None
                            gap = True
                            delay = retry_delay(failure_index)
                            failure_index += 1
                            retry_at = time.monotonic() + delay
                            state.update(state='reconnecting', message=str(error), retries=state['retries'] + 1, retry_delay_s=delay)
                            store.event('disconnected', dict(error=str(error), delay_s=delay))
            if client is None and now >= retry_at:
                state.update(state='connecting', message='Opening device', retry_delay_s=None)
                publish( dict(state, utc=utc_now()))
                try:
                    client = DemoClient(store.previous) if mode == 'demo' else WorkerClient(serial, clock)
                    connection_id = uuid.uuid4().hex
                    immutable_json(Path(path) / 'connections' / f'{connection_id}.json', client.info)
                    store.event('connected', dict(connection_id=connection_id, info=client.info))
                    # Drop stale estimator values across connections; raw historical batches remain saved.
                    records, last_rt = [], None
                    connected_since = time.monotonic()
                    successful_spectra = 0
                    next_spectrum, next_buffer = connected_since, connected_since
                    state.update(state='recording', message='Connected', retry_delay_s=None)
                except DeviceFailure as error:
                    delay = retry_delay(failure_index)
                    failure_index += 1
                    retry_at = time.monotonic() + delay
                    gap = True
                    state.update(state='reconnecting', message=str(error), retries=state['retries'] + 1,
                                 retry_delay_s=delay, next_retry_utc=dt.datetime.fromtimestamp(time.time() + delay, dt.timezone.utc).isoformat())
                    store.event('connection_retry', dict(error=str(error), delay_s=delay, failure_index=failure_index))
            if client is not None:
                try:
                    if test_reconnect_at is not None and not tested_reconnect and now - start >= test_reconnect_at:
                        tested_reconnect = True
                        raise DeviceFailure('TEST: deliberate transport close/reopen; cable remains connected')
                    if now >= next_buffer:
                        msg = client.request('buffer')
                        batch = dict(msg, connection_id=connection_id, boot_id=boot)
                        store.append_telemetry(batch)  # Persist before using decoded values.
                        observation=observation_from_batch(batch)
                        if observation:
                            last_rt=observation
                            records.append(observation)
                            state['device_projection_lead_s']=observation['device_projection_lead_s']
                            state['telemetry_time_basis']=observation['timestamp_basis']
                        for r in msg['result']['records']:
                            # First exchange status records can carry the previous timestamp base.
                            # Preserve them, but never use future-dated records in live averages.
                            future = timestamp(r['dt']) > timestamp(msg['utc_after']) + 1
                            if future and r['type'] not in ('RareData', 'Event','RealTimeData'):
                                continue
                            if r['type'] == 'Event':
                                if store.previous and timestamp(r['dt'])<midpoint(store.previous) and not future:
                                    store.event('historical_device_event',r)
                                    continue
                                store.event('device_event', r)
                                if r['event'] in (0, 1, 2, 19):
                                    generation += 1
                                if r['event'] == 4:
                                    dose_reset_seen = True
                                    store.set_state('dose_reset_seen', True)
                            elif r['type'] == 'RealTimeData':
                                pass  # Already selected as a reception-time observation; never discard for clock projection drift.
                            elif r['type'] == 'RareData':
                                observation,stale = select_rare(last_rare,r,msg['utc_after'])
                                if stale:
                                    store.event(stale,dict(record=r,previous=last_rare))
                                    continue
                                if last_rare and r['dose'] < last_rare['dose']:
                                    dose_reset_seen = True
                                    store.set_state('dose_reset_seen', True)
                                    store.event('dose_counter_decreased', dict(previous=last_rare, current=r))
                                last_rare = observation
                                if dose_baseline is None:
                                    dose_baseline = observation
                                    store.set_state('dose_baseline', dose_baseline)
                        warning = msg['result']['decode_warning']
                        if warning:
                            store.event('decoder_warning', dict(warning=warning, connection_id=connection_id))
                            state['decoder_warning'] = warning
                        cutoff = timestamp(msg['utc_after']) - max(config['interval_s'] * 3, 120)
                        records = [r for r in records if timestamp(r['dt']) >= cutoff]
                        # Advance from deadline, not from read completion. Missed polls are recorded.
                        next_buffer += 1
                        if next_buffer < time.monotonic():
                            store.event('telemetry_poll_late', dict(lateness_s=time.monotonic() - next_buffer))
                            next_buffer = time.monotonic() + 1
                    if time.monotonic() >= next_spectrum:
                        msg = client.request('spectrum')
                        snapshot = make_snapshot(msg, serial, boot, connection_id, generation, gap, last_rt, last_rare,
                                                 dose_baseline=dose_baseline, dose_reset_seen=dose_reset_seen)
                        if store.previous:
                            snapshot['telemetry_bin'] = weighted_telemetry(records, midpoint(store.previous), midpoint(snapshot))
                            snapshot['telemetry_bin']['timestamp_basis']='host_reception_utc'
                            tb = snapshot['telemetry_bin']
                            dose_integral += tb.get('observed_integrated_dose_uSv', 0.)
                            dose_observed_s += tb.get('observed_duration_s', 0.)
                            dose_elapsed_s += max(0., midpoint(snapshot) - midpoint(store.previous))
                        snapshot.update(derived_observed_dose_uSv=dose_integral,
                                        derived_observed_dose_coverage=dose_observed_s / dose_elapsed_s if dose_elapsed_s else 0.,
                                        derived_observed_dose_elapsed_s=dose_elapsed_s)
                        saved, derived = store.append_snapshot(snapshot)
                        state.update(state='recording', last_snapshot=saved, last_interval=derived,
                                     snapshots=store.next_seq, message=', '.join(derived['quality']))
                        gap = False
                        successful_spectra += 1
                        if time.monotonic() - connected_since >= 60 and successful_spectra >= 3:
                            failure_index = 0  # A successful open alone does not reset exponential backoff.
                        next_spectrum += config['interval_s']
                        if next_spectrum < time.monotonic():
                            store.event('spectrum_deadline_missed', dict(lateness_s=time.monotonic() - next_spectrum))
                            next_spectrum += (int((time.monotonic() - next_spectrum) / config['interval_s']) + 1) * config['interval_s']
                except DeviceFailure as error:
                    client.close()
                    client = None
                    delay = retry_delay(failure_index)
                    failure_index += 1
                    retry_at = time.monotonic() + delay
                    gap = True
                    state.update(state='reconnecting', message=str(error), retries=state['retries'] + 1,
                                 retry_delay_s=delay, next_retry_utc=dt.datetime.fromtimestamp(time.time() + delay, dt.timezone.utc).isoformat())
                    store.event('disconnected', dict(error=str(error), delay_s=delay, failure_index=failure_index))
            if time.monotonic() >= next_status:
                state['connected_elapsed_s'] = time.monotonic() - connected_since if client else None
                publish( dict(state, utc=utc_now(), uptime_s=time.monotonic() - start))
                next_status = time.monotonic() + 1
            time.sleep(.05)
    except BaseException as error:
        outcome = 'error'
        # Automatic restart must never conceal invalid/missing raw data or a disk failure.
        permanent = isinstance(error,(OSError,sqlite3.Error,ValueError)) or (
            isinstance(error,RuntimeError) and any(word in str(error) for word in ('another','already owns','end has passed')))
        exit_code = 2 if permanent else 1
        state['failure_class'] = 'durable_storage_or_validation' if permanent else 'unexpected_exception'
        state['message'] = f'{type(error).__name__}: {error}'
        state['traceback'] = traceback.format_exc()
        print(state['traceback'], file=sys.stderr, flush=True)
        if store:
            try:
                store.event('fatal_error', dict(error=state['message'], traceback=state['traceback']))
            except Exception:
                pass
    finally:
        if client:
            try:
                client.close()
            except Exception as error:
                print(f'Worker cleanup failed: {error}',file=sys.stderr,flush=True)
        if store:
            try:
                store.event('recorder_ended', dict(outcome=outcome, boot_id=boot))
                store.set_state('recorder', dict(boot_id=boot, pid=os.getpid(), status=outcome))
                publish(dict(state,state=outcome,utc=utc_now(),uptime_s=time.monotonic()-start),force=True)
            except Exception as error:
                exit_code = 2
                print(f'Durable shutdown failed: {error}',file=sys.stderr,flush=True)
            finally:
                try:
                    store.close()
                except Exception as error:
                    exit_code = 2
                    print(f'Storage cleanup failed: {error}',file=sys.stderr,flush=True)
        if mutex:
            mutex.close()
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:
            pass
        if publisher:
            publisher.close(dict(state,state=outcome,utc=utc_now(),uptime_s=time.monotonic()-start))
    return exit_code


def make_snapshot(msg, serial, boot, connection, generation, gap, realtime, rare, final=False,
                  dose_baseline=None, dose_reset_seen=False):
    experiment_dose = None
    if rare and dose_baseline and not dose_reset_seen and rare['dose'] >= dose_baseline['dose']:
        experiment_dose = (rare['dose'] - dose_baseline['dose']) * DOSE_R_TO_USV
    return dict(serial=serial, boot_id=boot, connection_id=connection, spectrum_generation=generation,
                had_disconnect=gap, final_partial_interval=final, spectrum=msg['result'],
                utc_before=msg['utc_before'], utc_after=msg['utc_after'],
                monotonic_before=msg['monotonic_before'], monotonic_after=msg['monotonic_after'],
                host_utc_before=msg.get('host_utc_before', msg['utc_before']),
                host_utc_after=msg.get('host_utc_after', msg['utc_after']), clock=msg.get('clock'),
                realtime=realtime, accumulated_dose=rare,
                device_experiment_dose_uSv=experiment_dose, dose_baseline=dose_baseline, dose_reset_seen=dose_reset_seen,
                read_time_halfwidth_s=max(0., (timestamp(msg['utc_after']) - timestamp(msg['utc_before'])) / 2),
                device_time_quantization_s=1., utc_accuracy='Monotonic UTC reference with saved SNTP measurement, or explicitly unverified host UTC fallback')


def make_config(serial, interval, seconds, mode, label):
    now = dt.datetime.now(dt.timezone.utc)
    return dict(schema=1, software_version=VERSION, serial=serial, interval_s=interval, duration_s=seconds,
                created_utc=now.isoformat(), planned_end_utc=(now + dt.timedelta(seconds=seconds)).isoformat() if seconds else None,
                mode=mode, label=label, versions=versions(), retry_initial_s=1, retry_cap_s=60,
                retry_reset='60s stable connection and at least 3 saved spectra',
                spectrum_source='VS.SPECTRUM cumulative channel counts',
                raw_dose_unit='R', raw_dose_rate_unit='R/h', display_dose_unit='uSv',
                dose_conversion='instrument convention 1 R -> 0.01 Sv',
                time_note='Monotonic polling; UTC request/response bounds; device elapsed time for spectral cps',
                processing='No smoothing, normalization, rebinning, baseline subtraction, or spectral reset')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--serial', default='RC-103-006489')
    parser.add_argument('--interval', type=float, default=10.)
    parser.add_argument('--seconds', type=float, default=604800.)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--label', default='')
    parser.add_argument('--test-reconnect-at', type=float, default=None)
    parser.add_argument('--stop-request', type=Path)
    args = parser.parse_args()
    if not 1 <= args.interval <= 600 or args.seconds < 0:
        parser.error('Interval must be 1..600 seconds; duration must be >=0')
    config = None if args.resume else make_config('SIMULATED' if args.demo else args.serial, args.interval, args.seconds,
                                                  'demo' if args.demo else 'device', args.label)
    return record(args.run.resolve(), config, args.test_reconnect_at, args.stop_request)


if __name__ == '__main__':
    mp.freeze_support()
    raise SystemExit(main())
