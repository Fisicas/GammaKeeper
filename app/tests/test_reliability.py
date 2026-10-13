import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bootstrap
import datetime as dt
import json
import os
import signal
import sqlite3
import struct
import subprocess
import tempfile
import time
import unittest

from analysis import reconstruct
from runtime import python_command
from status_cache import StatusPublisher
from telemetry_decoder import decode_buffer
from saved_status import load_status
from supervisor import ProgressWatchdog
from storage import RunStore,immutable_json
from recorder import make_config
from worker_client import WorkerClient,DeviceFailure
from status_observations import select_rare
from phyton import PhytonTransport
from radiacode.bytes_buffer import BytesBuffer
from test_core import snapshot

ROOT = Path(__file__).resolve().parents[1]
# Byte-exact copies of workspace evidence; provenance in tests/fixtures/README.md.
FIXTURES = Path(__file__).resolve().parent/'fixtures'


def hung_after_ready(pipe,serial,clock):
    pipe.send(dict(kind='ready',info=dict(serial=serial)))
    pipe.recv()
    time.sleep(30)


def wait_until(check, timeout=15):
    end = time.monotonic()+timeout
    while time.monotonic()<end:
        value = check()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('Condition deadline exceeded')


class Reliability(unittest.TestCase):
    def setUp(self):
        self.test_root = ROOT/'validation'/'reliability_temp'
        self.test_root.mkdir(parents=True,exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.test_root)
        self.path = Path(self.temp.name)

    def tearDown(self):
        assert self.path.resolve().parent==self.test_root.resolve()
        self.temp.cleanup()

    def test_reproduce_overnight_status_replace_error(self):
        status,temporary = self.path/'status.json',self.path/'status.partial'
        status.write_bytes(b'{"old":true}')
        temporary.write_bytes(b'{"new":true}')
        with status.open('rb') as held:
            held.read(1)
            with self.assertRaises(PermissionError) as caught:
                os.replace(temporary,status)
            self.assertEqual(caught.exception.winerror,5)

    def test_gui_reads_journal_even_with_corrupt_status_cache(self):
        run=self.path/'bad_cache'
        store=RunStore(run,make_config('SIMULATED',1,0,'demo','cache corruption test'))
        store.append_snapshot(snapshot(0,[100,200]))
        store.set_state('ui_status',dict(mode='demo',serial='SIMULATED',state='recording',pid=os.getpid(),
                                      boot_id='test',utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        store.close()
        (run/'status.json').write_bytes(b'invalid JSON')
        with (run/'status.json').open('rb') as held:
            held.read(1)
            state=load_status(run)
        self.assertEqual(state['state'],'recording')
        self.assertEqual(state['snapshots'],1)

    def test_cache_lock_is_nonfatal_and_recovers(self):
        publisher = StatusPublisher(self.path)
        publisher.submit(dict(n=0))
        wait_until(lambda:(self.path/'status.json').exists())
        with (self.path/'status.json').open('rb') as held:
            held.read(1)
            for i in range(1,30):
                publisher.submit(dict(n=i))
            wait_until(lambda:publisher.failures>0)
            self.assertTrue(publisher.thread.is_alive())
            self.assertLessEqual(publisher.pending.qsize(),1)
        publisher.submit(dict(n=30))
        wait_until(lambda:json.loads((self.path/'status.json').read_text())['n']==30)
        publisher.close()
        self.assertFalse(publisher.thread.is_alive())

    def test_empty_and_unknown_payload_preserve_valid_prefix(self):
        base = dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
        self.assertEqual(decode_buffer(b'',base,'')['records'],[])
        raw = struct.pack('<BBBi',255,0,0,-12800)+struct.pack('<ffHHHB',3.,1e-5,10,20,0,0)
        raw += struct.pack('<BBBi',0,8,99,0)+b'undecodable'
        decoded = decode_buffer(raw,base,'')
        self.assertEqual(len(decoded['records']),1)
        self.assertEqual(decoded['records'][0]['count_rate'],3.)
        self.assertEqual(decoded['raw_hex'],raw.hex())
        self.assertTrue(decoded['decode_warning'])

    def test_exact_overnight_decoder_fixture(self):
        fixture = json.loads((FIXTURES/'decoder_fixture_batch_2537.json').read_text())
        raw = bytes.fromhex(fixture['batch']['result']['raw_hex'])
        config_file = FIXTURES/'v01_connection_174ad44e105f4749a9121ede64272f90.json'
        config = json.loads(config_file.read_text())['configuration']
        decoded = decode_buffer(raw,dt.datetime(2026,1,1,tzinfo=dt.timezone.utc),config)
        self.assertIsNone(decoded['decode_warning'])
        self.assertEqual(decoded['parsed_prefix_bytes'],len(raw))
        self.assertEqual(decoded['records'][-1]['type'],'DoseCounter')
        self.assertEqual(decoded['records'][-1]['dose_counter_raw'],struct.unpack('<I',raw[-6:-2])[0])
        self.assertGreater(len(decoded['records']),1)

    def test_unknown_group4_schema_is_not_guessed(self):
        raw = struct.pack('<BBBiIH',0,0,4,0,42,0)
        result = decode_buffer(raw,dt.datetime.now(dt.timezone.utc),'')
        self.assertEqual(result['records'],[])
        self.assertTrue(result['decode_warning'])

    def test_request_timeout_terminates_hung_device_owner(self):
        client = WorkerClient('TEST',target=hung_after_ready,startup_timeout=10)
        start = time.monotonic()
        try:
            with self.assertRaises(DeviceFailure):
                client.request('spectrum',timeout=.25)
            self.assertLess(time.monotonic()-start,1.)
        finally:
            client.close()
        self.assertTrue(client._closed)
        client.close()  # Idempotent.

    def test_watchdog_uses_monotonic_progress_not_wall_time(self):
        watch = ProgressWatchdog(timeout=180,startup_grace=300)
        start = watch.started
        self.assertFalse(watch.observe((1,10,0),start))
        self.assertFalse(watch.observe((2,10,0),start+170))
        self.assertFalse(watch.observe((2,10,0),start+340))
        self.assertTrue(watch.observe((2,10,0),start+351))

    def test_historical_counter_is_not_a_dose_reset(self):
        previous=dict(dt='2026-10-07T13:19:57.386478+00:00',duration=3197764,dose=.01015899982303381)
        historical=dict(dt='2026-10-07T05:28:54.796523+00:00',duration=3169376,dose=.010115000419318676)
        selected,reason=select_rare(previous,historical,'2026-10-07T13:19:58+00:00')
        self.assertEqual(selected,previous)
        self.assertEqual(reason,'historical_status_ignored')
        fresh_reset=dict(dt='2026-10-07T13:19:59+00:00',duration=0,dose=0.)
        selected,reason=select_rare(previous,fresh_reset,'2026-10-07T13:19:59+00:00')
        self.assertIsNone(reason)
        self.assertEqual(selected['dose'],0.)

    def test_zero_length_and_split_usb_headers(self):
        transport=PhytonTransport.__new__(PhytonTransport)
        transport.BytesBuffer=BytesBuffer
        data=struct.pack('<I',8)+b'12345678'
        replies=iter([b'',data[:2],data[2:7],data[7:]])
        transport._io=lambda writing,value,timeout: len(value) if writing else next(replies)
        self.assertEqual(transport.execute(b'request').data(),b'12345678')

    def test_repeated_empty_usb_packets_fail_with_bounded_reads(self):
        transport=PhytonTransport.__new__(PhytonTransport)
        transport.BytesBuffer=BytesBuffer
        calls=[]
        def io(writing,value,timeout):
            if writing:
                return len(value)
            calls.append(timeout)
            return b''
        transport._io=io
        with self.assertRaisesRegex(OSError,'Three empty'):
            transport.execute(b'request')
        self.assertEqual(len(calls),3)

    def test_transient_raw_commit_lock_retries_without_overwrite(self):
        from unittest.mock import patch
        real=os.rename
        attempts=[]
        def transient(source,target):
            attempts.append(1)
            if len(attempts)<3:
                raise PermissionError('transient commit lock')
            return real(source,target)
        file=self.path/'raw.json'
        with patch('storage.os.rename',side_effect=transient):
            immutable_json(file,dict(counts=[1,2,3]))
        self.assertEqual(len(attempts),3)
        self.assertEqual(json.loads(file.read_text())['counts'],[1,2,3])
        with self.assertRaises(FileExistsError):
            immutable_json(file,dict(counts=[99]))

    def test_gui_never_previews_modified_raw_snapshot(self):
        run=self.path/'tampered_preview'
        store=RunStore(run,make_config('SIMULATED',1,0,'demo','preview integrity'))
        store.append_snapshot(snapshot(0,[100,200]))
        store.set_state('ui_status',dict(mode='demo',serial='SIMULATED',state='stopped',pid=0,
                                      boot_id='test',utc=dt.datetime.now(dt.timezone.utc).isoformat()))
        store.close()
        raw=next((run/'spectra').rglob('*.json'))
        value=json.loads(raw.read_text())
        value['spectrum']['counts'][0]+=1
        raw.write_text(json.dumps(value))
        state=load_status(run)
        self.assertNotIn('last_snapshot',state)
        self.assertIn('SHA-256 mismatch',state['raw_preview_error'])

    def test_recording_continues_under_real_windows_status_lock(self):
        run = self.path/'locked_run'
        log = (self.path/'recorder.log').open('wb')
        child = subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'recorder.py'),'--run',str(run),
                                  '--demo','--seconds','18','--interval','1'],cwd=ROOT,stdout=log,stderr=log,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            wait_until(lambda:(run/'status.json').exists())
            with (run/'status.json').open('rb') as held:
                held.read(1)
                time.sleep(8)
                state = load_status(run)
                self.assertEqual(state['state'],'recording')
                self.assertGreaterEqual(state['snapshots'],6)
                self.assertIsNone(child.poll())
            self.assertEqual(child.wait(timeout=25),0)
            state = load_status(run)
            self.assertEqual(state['state'],'completed')
            db = sqlite3.connect(run/'run.sqlite')
            try:
                kinds = [r[0] for r in db.execute('SELECT kind FROM events')]
                self.assertIn('status_cache_unavailable',kinds)
                self.assertIn('status_cache_recovered',kinds)
                self.assertNotIn('fatal_error',kinds)
            finally:
                db.close()
        finally:
            if child.poll() is None:
                child.terminate(); child.wait(timeout=5)
            log.close()

    def test_supervisor_restarts_killed_recorder_and_stop_is_final(self):
        run = self.path/'supervised_run'
        log = (self.path/'supervisor.log').open('wb')
        parent = subprocess.Popen([python_command(),'-X','utf8',str(ROOT/'supervisor.py'),'--run',str(run),
                                   '--demo','--seconds','0','--interval','1'],cwd=ROOT,stdout=log,stderr=log,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            def running():
                try:
                    s = load_status(run)
                    return s if s and s.get('snapshots',0)>=3 and s.get('supervisor') else None
                except (OSError,ValueError,sqlite3.Error):
                    return None
            first = wait_until(running)
            # Actual recorder PID, not an unrelated Python process or venv redirector.
            killed_pid = first['pid']
            # Terminate only the synthetic recorder child directly. This avoids a
            # taskkill subprocess and still exercises the supervisor restart path.
            os.kill(killed_pid,signal.SIGTERM)
            def restarted():
                s = running()
                return s if s and s['pid']!=killed_pid and s['supervisor']['restarts']>=1 and s.get('snapshots',0)>=5 else None
            second = wait_until(restarted,25)
            Path(second['stop_request']).write_text('test stop')
            self.assertEqual(parent.wait(timeout=20),0)
            final = load_status(run)
            self.assertEqual(final['state'],'stopped')
            self.assertEqual(final['supervisor']['status'],'stopped')
            self.assertEqual(final['supervisor']['restarts'],1)
        finally:
            if parent.poll() is None:
                latest = load_status(run)
                if latest and latest.get('stop_request'):
                    Path(latest['stop_request']).write_text('test cleanup stop')
                    parent.wait(timeout=20)
                else:
                    subprocess.run(['taskkill','/PID',str(parent.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                    parent.wait(timeout=5)
            log.close()

    def test_corrupt_raw_data_suppresses_automatic_restart(self):
        run = self.path/'bad_run'
        config = make_config('SIMULATED',1,0,'demo','integrity fault test')
        store = RunStore(run,config)
        saved,_ = store.append_snapshot(snapshot(0,[100,200]))
        store.close()
        next((run/'spectra').rglob('*.json')).write_text('{}')
        result = subprocess.run([python_command(),'-X','utf8',str(ROOT/'supervisor.py'),'--run',str(run),'--resume'],
                                capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,2)
        db = sqlite3.connect(run/'run.sqlite')
        try:
            state = load_status(run)['supervisor']
            self.assertEqual(state['status'],'attention_required')
            self.assertEqual(state['restarts'],0)
        finally:
            db.close()


if __name__=='__main__':
    unittest.main(verbosity=2)
