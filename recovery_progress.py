"""Recorder-only optional recovery progress, independent of the raw journal."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import time


def report(run,phase,verified,total):
    try:
        db=sqlite3.connect(Path(run)/'recovery.sqlite',timeout=.05)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT)')
            value=dict(pid=os.getpid(),phase=phase,verified=verified,total=total,
                       monotonic_s=time.monotonic(),utc=dt.datetime.now(dt.timezone.utc).isoformat())
            with db:
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('recovery',json.dumps(value)))
        finally:
            db.close()
    except (OSError,sqlite3.Error):
        pass


def read(run):
    file=Path(run).resolve()/'recovery.sqlite'
    if not file.exists():
        return None
    try:
        db=sqlite3.connect(file.as_uri()+'?mode=ro',uri=True,timeout=.05)
        try:
            row=db.execute("SELECT value FROM state WHERE key='recovery'").fetchone()
            return json.loads(row[0]) if row else None
        finally:
            db.close()
    except (OSError,sqlite3.Error,ValueError):
        return None
