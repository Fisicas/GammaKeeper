"""Optional GUI preferences; failure never affects a recorder or raw files."""
import json
import os
import sqlite3
from pathlib import Path


def test_mode():
    # GAMMAKEEPER_TEST_MODE is the current name; RADIALOG_TEST_MODE is still honored.
    return '1' in (os.environ.get('GAMMAKEEPER_TEST_MODE'), os.environ.get('RADIALOG_TEST_MODE'))


def read(root):
    if test_mode():return {}
    file=Path(root)/'gui_preferences.sqlite'
    if not file.exists():return {}
    try:
        db=sqlite3.connect(file.as_uri()+'?mode=ro',uri=True,timeout=.05)
        try:
            row=db.execute('SELECT value FROM preferences WHERE key=?',('last_view',)).fetchone()
            return json.loads(row[0]) if row else {}
        finally:db.close()
    except (OSError,sqlite3.Error,ValueError):return {}


def save(root,value):
    if test_mode():return
    try:
        db=sqlite3.connect(Path(root)/'gui_preferences.sqlite',timeout=.05)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY,value TEXT)')
            with db:db.execute('INSERT OR REPLACE INTO preferences VALUES(?,?)',('last_view',json.dumps(value)))
        finally:db.close()
    except (OSError,sqlite3.Error):pass
