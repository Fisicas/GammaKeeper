"""Experiment notes and the seal time, stored beside a run in annotations.sqlite (plan S1).

The recorder's journal is never written: the GUI is the only writer of this file. Notes are
append-only (database triggers refuse UPDATE and DELETE); marking the seal again adds a newer
seal note, and the most recently entered one is the experiment's time zero.
"""
import csv
import datetime as dt
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

from analysis import VERSION

FILE = 'annotations.sqlite'
KINDS = [('seal', 'Seal time (time zero)'), ('canister_placed', 'Canister placed on detector'),
         ('canister_removed', 'Canister removed'), ('background_start', 'Background measurement start'),
         ('note', 'Note')]
KIND_LABELS = dict(KINDS)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS notes(
    id INTEGER PRIMARY KEY,
    utc REAL NOT NULL,              -- event time, seconds since the epoch
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    entered_utc REAL NOT NULL,      -- when the note was written (host clock)
    time_basis TEXT NOT NULL,       -- recorder_sntp_reference, host_clock or entered_by_user
    software_version TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS notes_no_update BEFORE UPDATE ON notes BEGIN SELECT RAISE(ABORT, 'notes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS notes_no_delete BEFORE DELETE ON notes BEGIN SELECT RAISE(ABORT, 'notes are append-only'); END;
CREATE TABLE IF NOT EXISTS note_metadata(note_id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS metadata_no_update BEFORE UPDATE ON note_metadata BEGIN SELECT RAISE(ABORT, 'metadata are append-only'); END;
CREATE TRIGGER IF NOT EXISTS metadata_no_delete BEFORE DELETE ON note_metadata BEGIN SELECT RAISE(ABORT, 'metadata are append-only'); END;
'''


def iso(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat(timespec='milliseconds')


def add(run, utc, kind, text='', time_basis='host_clock', metadata=None):
    """Append one note and return its id."""
    if kind not in KIND_LABELS:
        raise ValueError(f'Unknown note kind: {kind}')
    run = Path(run).resolve()
    if (run / 'BACKUP_PLAN.json').exists():
        raise ValueError('Backup annotations are frozen; add notes to the original recording')
    if not math.isfinite(float(utc)):
        raise ValueError('Event time must be finite')
    if not (run / 'manifest.json').exists():
        raise ValueError('Notes can only be added to a recorded run')
    db = sqlite3.connect(Path(run) / FILE, timeout=2)
    try:
        db.executescript(SCHEMA)
        with db:
            cursor = db.execute('INSERT INTO notes(utc,kind,text,entered_utc,time_basis,software_version) VALUES(?,?,?,?,?,?)',
                                (float(utc), kind, str(text), time.time(), time_basis, VERSION))
            db.execute('INSERT INTO note_metadata VALUES(?,?)',
                       (cursor.lastrowid, json.dumps(metadata or {}, allow_nan=False)))
        return cursor.lastrowid
    finally:
        db.close()


def load(run):
    """All notes, oldest event first, as dicts; [] when the run has none."""
    path = Path(run).resolve() / FILE
    if not path.exists():
        legacy = path.with_name('annotations.csv')
        if legacy.exists() and (path.parent / 'BACKUP_PLAN.json').exists():
            with legacy.open(encoding='utf-8', newline='') as handle:
                return [dict(id=int(r['id']), utc=float(r['utc_seconds']), kind=r['kind'], text=r['text'],
                             entered_utc=dt.datetime.fromisoformat(r['entered_utc']).timestamp(),
                             time_basis=r['time_basis'], software_version=r['software_version'],
                             metadata=json.loads(r.get('metadata_json') or '{}')) for r in csv.DictReader(handle)]
        return []
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    try:
        rows = db.execute('SELECT id,utc,kind,text,entered_utc,time_basis,software_version FROM notes ORDER BY utc,id').fetchall()
        metadata = dict(db.execute('SELECT note_id,payload FROM note_metadata')) if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='note_metadata'").fetchone() else {}
    finally:
        db.close()
    keys = ('id', 'utc', 'kind', 'text', 'entered_utc', 'time_basis', 'software_version')
    return [dict(zip(keys, row), metadata=json.loads(metadata.get(row[0], '{}'))) for row in rows]


def seal(notes):
    """The current seal note (most recently entered), or None."""
    seals = [note for note in notes if note['kind'] == 'seal']
    return max(seals, key=lambda note: note['id']) if seals else None


def export_csv(run, target, notes=None):
    """Write every note to target (CSV with UTC ISO times); returns the number written."""
    notes = load(run) if notes is None else notes
    if not notes:
        return 0
    current = seal(notes)
    with open(target, 'x', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['id', 'utc', 'utc_seconds', 'kind', 'text', 'current_seal', 'entered_utc', 'time_basis', 'software_version', 'metadata_json'])
        for note in notes:
            writer.writerow([note['id'], iso(note['utc']), f"{note['utc']:.3f}", note['kind'], note['text'],
                             bool(current and note['id'] == current['id']), iso(note['entered_utc']),
                             note['time_basis'], note['software_version'], json.dumps(note.get('metadata', {}), sort_keys=True)])
    return len(notes)


def export_snapshot(run, target):
    """Freeze one annotation horizon in a hash-verifiable CSV/JSON pair."""
    from storage import immutable_json, canonical
    target = Path(target)
    notes = load(run)
    current = seal(notes)
    export_csv(run, target / 'annotations.csv', notes)
    identity = dict(notes=notes, last_note_id=max((n['id'] for n in notes), default=0),
                    selected_seal_id=current['id'] if current else None,
                    snapshot_utc=iso(time.time()), independent_of_acquisition_snapshot=True)
    identity['notes_sha256'] = hashlib.sha256(canonical(notes)).hexdigest()
    immutable_json(target / 'ANNOTATIONS.json', identity)
    return identity
