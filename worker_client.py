"""Deadline-controlled IPC, including stalled partial pipe messages."""
import multiprocessing as mp
import queue
import threading
import time
from device import io_worker
from runtime import PYTHONW


class DeviceFailure(Exception):
    pass


class WorkerClient:
    def __init__(self, serial, clock=None, target=io_worker, startup_timeout=15):
        ctx = mp.get_context('spawn')
        # GUI may be launched with pythonw; explicitly preserve the shared venv.
        mp.set_executable(str(PYTHONW))
        self.pipe, child = ctx.Pipe()
        self.process = ctx.Process(target=target,args=(child,serial,clock),daemon=True)
        try:
            self.process.start()
        except (OSError,RuntimeError) as error:
            self.pipe.close(); child.close()
            raise DeviceFailure(f'Cannot start device worker: {error}') from error
        child.close()
        self._start_pump()
        try:
            ready = self.receive(startup_timeout)
            if ready.get('kind') != 'ready' or not isinstance(ready.get('info'),dict):
                raise DeviceFailure('Invalid startup reply')
            self.info = ready['info']
        except BaseException:
            self.close()
            raise

    def _start_pump(self):
        self._responses = queue.Queue(maxsize=2)
        self._commands = queue.Queue(maxsize=1)
        self._closed = False
        self._pump_thread = threading.Thread(target=self._pump,name='device-ipc',daemon=True)
        self._pump_thread.start()

    def _pump(self):
        try:
            # recv() itself can block after poll() sees only a partial message.
            # Only this disposable thread performs pipe send/recv.
            self._responses.put(self.pipe.recv())
            while not self._closed:
                command = self._commands.get()
                self.pipe.send(command)
                if command == 'close':
                    return
                self._responses.put(self.pipe.recv())
        except BaseException as error:
            try:
                self._responses.put_nowait(dict(kind='error',error=f'IPC {type(error).__name__}: {error}'))
            except queue.Full:
                pass

    def receive(self, timeout=12):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            try:
                value = self._responses.get(timeout=min(.1,max(.001,deadline-time.monotonic())))
            except queue.Empty:
                if not self.process.is_alive():
                    raise DeviceFailure(f'Worker exited with code {self.process.exitcode}')
                continue
            if not isinstance(value,dict):
                raise DeviceFailure('Invalid IPC reply type')
            if value.get('kind') == 'error':
                raise DeviceFailure(value.get('error','Unknown worker error'))
            return value
        raise DeviceFailure(f'Device worker exceeded {timeout}s deadline')

    def request(self, command, timeout=12):
        if self._closed:
            raise DeviceFailure('Worker is closed')
        try:
            self._commands.put_nowait(command)
        except queue.Full as error:
            raise DeviceFailure('Device command queue is blocked') from error
        value = self.receive(timeout)
        expected = command.get('kind') if isinstance(command,dict) else command
        if value.get('kind') != expected:
            raise DeviceFailure(f'Unexpected reply {value.get("kind")}; expected {expected}')
        return value

    def close(self):
        if getattr(self,'_closed',False):
            return
        self._closed = True
        try:
            self._commands.put_nowait('close')
        except queue.Full:
            pass
        try:
            self.process.join(.5)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(2)
            self._pump_thread.join(.5)
        finally:
            self.pipe.close()
            if not self.process.is_alive():
                self.process.close()  # Avoid per-retry Windows process-handle leaks.
