"""Background preview and accumulation tasks for the analysis views."""
from PySide6 import QtCore
from preview import read_preview


class PreviewSignals(QtCore.QObject):
    done=QtCore.Signal(object)
    snapshot=QtCore.Signal(object)
    progress=QtCore.Signal(str)


class PreviewTask(QtCore.QRunnable):
    def __init__(self,run,view,epoch,key,cancel,cache):
        super().__init__()
        self.signals=PreviewSignals()
        self.run_path,self.view,self.epoch,self.key,self.cancel,self.cache=run,view,epoch,key,cancel,cache

    def run(self):
        try:
            result=read_preview(self.run_path,self.view,self.epoch,progress=self.signals.progress.emit,
                                cancelled=self.cancel.is_set,cache_dir=self.cache,
                                snapshot_callback=lambda value:self.signals.snapshot.emit(
                                    dict(key=self.key,result=value)))
            message=dict(key=self.key,result=result,error=None)
        except Exception as error:
            message=dict(key=self.key,result=None,error=f'{type(error).__name__}: {error}')
        self.signals.done.emit(message)


class AccumulationTask(QtCore.QRunnable):
    def __init__(self,engine,key,cancel):
        super().__init__();self.engine=engine;self.key=key;self.cancel=cancel;self.signals=PreviewSignals()

    def run(self):
        try:
            cached=self.engine.restore_cache()
            published=False
            if cached:
                self.engine.validate_cached_endpoint()
                row=self.engine.result()
                if row is not None:
                    self.signals.snapshot.emit(dict(key=self.key,row=row,head=self.engine.last_seq,cached=True))
                    published=True
            while not self.engine.update(cancelled=self.cancel.is_set):
                if self.cancel.is_set():
                    self.signals.done.emit(dict(key=self.key,cancelled=True));return
                if not published:
                    row=self.engine.result()
                    if row is not None:
                        self.signals.snapshot.emit(dict(key=self.key,row=row,head=self.engine.last_seq,cached=False))
                        published=True
                self.signals.progress.emit(f'Accumulating verified intervals through spectrum {self.engine.last_seq:,} / {self.engine.head:,}')
            self.signals.done.emit(dict(key=self.key,row=self.engine.result(),head=self.engine.head,error=None))
        except Exception as error:
            self.signals.done.emit(dict(key=self.key,row=None,head=self.engine.head,error=f'{type(error).__name__}: {error}'))
