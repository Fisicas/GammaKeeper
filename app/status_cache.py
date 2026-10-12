"""Optional UI cache. Neither locks nor cache errors may end acquisition."""
import os
from pathlib import Path
import queue
import threading
import time
import uuid
from storage import canonical


class StatusPublisher:
    def __init__(self, path):
        self.path = Path(path) / 'status.json'
        self.temp = self.path.with_name('status.' + uuid.uuid4().hex + '.partial')
        self.pending = queue.Queue(maxsize=1)
        self.warnings = queue.SimpleQueue()
        self.closed = False
        self.failures = 0
        self.successes = 0
        self.last_warning = -float('inf')
        self.thread = threading.Thread(target=self._run, name='optional-status-cache', daemon=True)
        self.thread.start()

    def submit(self, value):
        if self.closed:
            return
        try:
            payload = canonical(value)
            self._replace_pending(payload)
        except Exception as error:
            self._warn(error, 'encode')

    def _replace_pending(self, payload):
        try:
            self.pending.get_nowait()
        except queue.Empty:
            pass
        try:
            self.pending.put_nowait(payload)
        except queue.Full:
            pass  # Another producer won; memory remains bounded.

    def _warn(self, error, stage):
        self.failures += 1
        now = time.monotonic()
        if now - self.last_warning >= 60:
            self.last_warning = now
            self.warnings.put(dict(kind='status_cache_unavailable', stage=stage,
                                   error=f'{type(error).__name__}: {error}',
                                   winerror=getattr(error,'winerror',None), failures=self.failures,
                                   note='Optional UI cache failed; scientific recording continues'))

    def _run(self):
        degraded = False
        while True:
            payload = self.pending.get()
            if payload is None:
                return
            try:
                with self.temp.open('wb') as file:
                    file.write(payload)
                    file.flush()
                for attempt in range(4):
                    try:
                        os.replace(self.temp, self.path)
                        break
                    except PermissionError:
                        if attempt == 3:
                            raise
                        time.sleep(.02 * 2**attempt)
                self.successes += 1
                if degraded:
                    self.warnings.put(dict(kind='status_cache_recovered', failures=self.failures))
                    degraded = False
            except Exception as error:
                degraded = True
                self._warn(error,'publish')
            finally:
                # Only this publisher's replaceable temporary is cleaned up.
                try:
                    self.temp.unlink(missing_ok=True)
                except OSError:
                    pass

    def close(self, final=None, timeout=1):
        if self.closed:
            return
        if final is not None:
            self.submit(final)
        deadline = time.monotonic() + timeout
        # Give the final state a bounded opportunity to be published.
        while not self.pending.empty() and time.monotonic() < deadline:
            time.sleep(.01)
        self.closed = True
        self._replace_pending(None)
        self.thread.join(max(0, deadline - time.monotonic()))
