"""Timestamped structured diagnostics, never a new acquisition dependency."""
import datetime as dt
import json
import os
import sys


def diagnostic(kind, **fields):
    value = dict(utc=dt.datetime.now(dt.timezone.utc).isoformat(), pid=os.getpid(), kind=kind, **fields)
    try:
        print(json.dumps(value, ensure_ascii=False, default=str), file=sys.stderr, flush=True)
    except OSError:
        pass
    return value
