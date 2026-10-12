"""Durable append-only scientific records and immutable spectrum files."""
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from analysis import reconstruct, midpoint, VERSION
from recovery_progress import report as report_recovery


class RecoveryCancelled(Exception):
    pass


def ordered_reads(files, workers=8):
    """Overlap file-open latency with at most 2*workers payloads; yield in sequence."""
    iterator=iter(files)
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='raw-verify-read') as pool:
        pending=deque()
        for file in iterator:
            pending.append((file,pool.submit(file.read_bytes)))
            if len(pending)>=workers*2:break
        while pending:
            file,future=pending.popleft()
            payload=future.result()
            next_file=next(iterator,None)
            if next_file is not None:pending.append((next_file,pool.submit(next_file.read_bytes)))
            yield file,payload


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def immutable_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f'Refusing to overwrite {path}')
    payload = canonical(value)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.partial')
    with temporary.open('xb') as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    # os.rename on Windows refuses an existing destination and is atomic on this volume.
    for attempt in range(7):
        try:
            os.rename(temporary,path)
            break
        except PermissionError:
            if attempt==6:
                raise
            time.sleep(.01*2**attempt)
    return hashlib.sha256(payload).hexdigest()


class DeviceMutex:
    def __init__(self, serial):
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
        self.api.CreateMutexW.restype = w.HANDLE
        self.api.ReleaseMutex.argtypes = [w.HANDLE]
        self.api.CloseHandle.argtypes = [w.HANDLE]
        # Mutex name shared with RadiaLog v0.1-v0.5.1 so old and new releases exclude each other; never rename.
        self.handle = self.api.CreateMutexW(None, True, 'Local\\RadiaLog_' + serial.replace('\\', '_'))
        err = ctypes.get_last_error()
        if not self.handle:
            raise ctypes.WinError(err)
        if err == 183:
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise RuntimeError('Another GammaKeeper or RadiaLog recorder already owns this serial number')

    def close(self):
        if self.handle:
            self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle)
            self.handle = None


class RunStore:
    def __init__(self, path, config=None, cancel=None):
        self.path = Path(path).resolve()
        self.cancel=cancel
        if config is not None:
            self.path.mkdir(parents=True, exist_ok=False)
            immutable_json(self.path / 'manifest.json', config)
        self.config = json.loads((self.path / 'manifest.json').read_text(encoding='utf-8'))
        # Also prevent two writers to this run, including demo vs physical device.
        import msvcrt
        self.lock = None
        try:
            self.lock = (self.path / '.writer.lock').open('a+b')
            self.lock.seek(0)
            if self.lock.read(1) == b'':
                self.lock.write(b'0')
                self.lock.flush()
            self.lock.seek(0)
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            if self.lock:
                self.lock.close()
                self.lock = None
            raise RuntimeError('This run is open in another recorder')
        try:
            self.db = sqlite3.connect(self.path / 'run.sqlite', timeout=5)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA busy_timeout=5000')
            self.db.executescript('''
              CREATE TABLE IF NOT EXISTS snapshots(seq INTEGER PRIMARY KEY, file TEXT UNIQUE NOT NULL,
                sha256 TEXT NOT NULL, utc REAL NOT NULL, epoch INTEGER NOT NULL, derived TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS telemetry(id INTEGER PRIMARY KEY, utc REAL NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, utc TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS telemetry_integrity(id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS telemetry_utc ON telemetry(utc);
            ''')
            self.previous = None
            self.epoch = 0
            self.next_seq = 0
            self.recovered_dose = dict(integral=0.,observed_s=0.,elapsed_s=0.)
            self.summary = dict(total_counts=0,device_elapsed_s=0.,intervals=0,quality_counts={},epochs=1,
                                normal_device_exposure_s=0.,aggregate_device_exposure_s=0.,other_valid_device_exposure_s=0.,invalid_wall_s=0.,
                                first_utc=None,last_utc=None,mean_spectral_rate_cps=None)
            self.recover()
        except BaseException:
            self.close()
            raise

    def check_space(self):
        # Stop safely before exhausting the disk. Never delete old runs.
        if shutil.disk_usage(self.path).free < 256 * 1024**2:
            raise OSError('Less than 256 MiB free: recording stopped to protect saved data')

    def recover(self):
        rows = {seq: (name, digest) for seq, name, digest in self.db.execute('SELECT seq,file,sha256 FROM snapshots')}
        report_recovery(self.path,'running',0,len(rows))
        last_report=time.monotonic()
        seen = set()
        # Sequence is authoritative; UTC can step backwards across a date boundary.
        files=sorted((self.path / 'spectra').glob('*/*.json'), key=lambda item: int(item.stem))
        for file,payload in ordered_reads(files):
            if self.cancel and self.cancel():
                report_recovery(self.path,'cancelled',self.next_seq,len(rows))
                raise RecoveryCancelled('Stop requested during integrity recovery')
            value = json.loads(payload)
            if not isinstance(value,dict) or not isinstance(value.get('seq'),int):
                raise ValueError(f'Invalid raw snapshot schema: {file}')
            seq = value['seq']
            if seq != int(file.stem):
                raise ValueError(f'Snapshot filename/sequence mismatch: {file}')
            if seq != self.next_seq:
                raise ValueError(f'Snapshot sequence broken at {file}; refusing to hide a missing file')
            digest = hashlib.sha256(payload).hexdigest()
            name = file.relative_to(self.path).as_posix()
            if seq in rows and rows[seq] != (name, digest):
                raise ValueError(f'Raw snapshot integrity mismatch: {file}')
            try:
                derived = reconstruct(self.previous, value, self.config['interval_s'], self.epoch)
            except (KeyError,TypeError,ValueError) as error:
                raise ValueError(f'Invalid raw snapshot data: {file}: {error}') from error
            if seq not in rows:
                # Crash between atomic raw-file commit and index commit: recover from raw.
                self.db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?)',
                                (seq, name, digest, midpoint(value), derived['epoch'], canonical(derived).decode()))
            self.previous, self.epoch = value, derived['epoch']
            self.update_summary(value, derived)
            tb=value.get('telemetry_bin') or {}
            self.recovered_dose['integral']+=tb.get('observed_integrated_dose_uSv',tb.get('integrated_dose_uSv') or 0.)
            self.recovered_dose['observed_s']+=tb.get('observed_duration_s',0.)
            if value.get('derived_observed_dose_elapsed_s') is not None:
                self.recovered_dose['elapsed_s']=value['derived_observed_dose_elapsed_s']
            self.next_seq += 1
            seen.add(seq)
            if time.monotonic()-last_report>=2:
                report_recovery(self.path,'running',self.next_seq,len(rows))
                last_report=time.monotonic()
        if set(rows) - seen:
            raise ValueError('Index references missing raw files; recording will not continue')
        self.db.commit()
        report_recovery(self.path,'complete',self.next_seq,len(rows))
        partials = list(self.path.rglob('*.partial'))
        if partials:
            self.event('recovery_partial_files', dict(files=[p.relative_to(self.path).as_posix() for p in partials]))

    def append_snapshot(self, snapshot):
        self.check_space()
        snapshot = dict(snapshot, seq=self.next_seq, schema=1, software_version=VERSION)
        date = snapshot['utc_after'][:10]
        file = self.path / 'spectra' / date / f'{self.next_seq:09d}.json'
        derived = reconstruct(self.previous, snapshot, self.config['interval_s'], self.epoch)
        digest = immutable_json(file, snapshot)
        self.update_summary(snapshot,derived)
        with self.db:
            self.db.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?)',
                            (self.next_seq, file.relative_to(self.path).as_posix(), digest,
                             midpoint(snapshot), derived['epoch'], canonical(derived).decode()))
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',
                            ('acquisition_summary',canonical(self.summary).decode()))
        self.previous, self.epoch = snapshot, derived['epoch']
        self.next_seq += 1
        return snapshot, derived

    def update_summary(self, snapshot, derived):
        value=self.summary
        value['first_utc'] = value['first_utc'] or snapshot['utc_after']
        value['last_utc'] = snapshot['utc_after']
        value['snapshots'] = snapshot['seq']+1
        value['epochs'] = derived['epoch']+1
        if derived['counts_delta'] is not None:
            value['total_counts'] += derived['total_counts']
            value['device_elapsed_s'] += derived['device_duration_s']
            value['intervals'] += 1
            flags=set(derived['quality'])
            field=('aggregate_device_exposure_s' if flags & {'gap_recovered_aggregate','reconnect_aggregate_continuity_inferred'} else
                   'normal_device_exposure_s' if flags <= {'normal','final_short_interval'} else 'other_valid_device_exposure_s')
            value[field]+=derived['device_duration_s']
        elif self.previous is not None:
            value['invalid_wall_s']+=max(0.,derived.get('wall_duration_s') or 0.)
        value['mean_spectral_rate_cps'] = value['total_counts']/value['device_elapsed_s'] if value['device_elapsed_s'] else None
        for key in derived['quality']:
            value['quality_counts'][key] = value['quality_counts'].get(key,0)+1

    def append_telemetry(self, batch):
        self.check_space()
        payload=canonical(batch)
        with self.db:
            cursor=self.db.execute('INSERT INTO telemetry(utc,payload) VALUES(?,?)',
                                   (dt.datetime.fromisoformat(batch['utc_after']).timestamp(),payload.decode()))
            self.db.execute('INSERT INTO telemetry_integrity VALUES(?,?)',
                            (cursor.lastrowid,hashlib.sha256(payload).hexdigest()))

    def event(self, kind, payload):
        with self.db:
            self.db.execute('INSERT INTO events(utc,kind,payload) VALUES(?,?,?)',
                            (dt.datetime.now(dt.timezone.utc).isoformat(), kind, canonical(payload).decode()))

    def set_state(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, canonical(value).decode()))

    def close(self):
        if getattr(self, 'db', None):
            self.db.execute('PRAGMA wal_checkpoint(PASSIVE)')
            self.db.close()
            self.db = None
        if getattr(self, 'lock', None):
            self.lock.close()
            self.lock = None
