"""Supervisor-only reporting database; it never writes the acquisition journal."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
from diagnostics import diagnostic


def save_health(run, state, event=None):
    run = Path(run)
    if not (run / 'run.sqlite').exists():
        return  # The recorder owns creation of the run directory.
    try:
        db = sqlite3.connect(run / 'supervisor.sqlite', timeout=.1)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                             'CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,utc TEXT,kind TEXT,payload TEXT);')
            with db:
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('supervisor', json.dumps(state, allow_nan=False)))
                if event:
                    db.execute('INSERT INTO events(utc,kind,payload) VALUES(?,?,?)',
                               (dt.datetime.now(dt.timezone.utc).isoformat(), event['kind'], json.dumps(event)))
        finally:
            db.close()
    except (OSError, sqlite3.Error) as error:
        diagnostic('supervisor_reporting_unavailable', error=str(error))


def read_health(run):
    path = Path(run).resolve() / 'supervisor.sqlite'
    if not path.exists():
        return None
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.1)
    try:
        row = db.execute("SELECT value FROM state WHERE key='supervisor'").fetchone()
        return json.loads(row[0]) if row else None
    finally:
        db.close()
