"""A raw-verified latest recording interval, independent of heatmap selection."""
import hashlib
import json
from pathlib import Path
import sqlite3
from analysis import reconstruct,midpoint
from export_run import readonly
import numpy as np
from collections import Counter

# Increment when accumulator persistence or reconstruction semantics change.
ACCUMULATION_CACHE_VERSION=1


def accumulation_cache_path(run,cache_root,start=None,end=None,epoch=None):
    run=Path(run).resolve()
    key=hashlib.sha256((str(run)+repr((start,end,epoch))).encode()).hexdigest()
    return Path(cache_root)/'accumulated'/f'{key}.sqlite'


def accumulated_from_preview_rows(rows, head=None, summary=None):
    """Build an exact whole-run channel sum from already verified display groups."""
    usable=[row for row in rows if row[2].get('counts_delta') is not None]
    if not usable:
        return None,None
    calibrations={tuple(row[3]['spectrum']['calibration_keV']) for row in usable}
    lengths={len(row[2]['counts_delta']) for row in usable}
    if len(calibrations)!=1 or len(lengths)!=1:
        return None,'The whole-run view spans different calibrations or channel counts. Select one epoch; channels were not resampled.'
    counts=np.zeros(next(iter(lengths)),dtype=np.int64)
    duration=wall=0.
    intervals=0
    epochs=set()
    starts=[];ends=[]
    for end,epoch,derived,saved in usable:
        counts+=np.asarray(derived['counts_delta'],dtype=np.int64)
        duration+=derived.get('device_duration_s') or 0.
        wall+=derived.get('wall_duration_s') or 0.
        first=saved.get('display_first_seq')
        last=saved.get('display_last_seq')
        intervals+=saved.get('display_interval_count') or (last-first+1 if first is not None and last is not None else 1)
        epochs.add(epoch)
        ends.append(end)
        starts.append(end-(derived.get('wall_duration_s') or 0.))
    values=counts.tolist();total=int(counts.sum())
    actual_start=min(starts) if starts else None
    actual_end=max(ends) if ends else None
    metadata=dict(included_intervals=intervals,device_duration_s=duration,
                  actual_start_utc=actual_start,actual_end_utc=actual_end,
                  requested_start_utc=None,requested_end_utc=None,
                  excluded_boundary_intervals=0,baseline_boundaries=0,
                  quality_counts=(summary or {}).get('quality_counts',{}),
                  epochs=sorted(epochs),source_head=head,total_counts=total,
                  selection_rule='Complete recorded intervals only; no splitting, smoothing, resampling or pre-recording counts')
    derived=dict(counts_delta=values,device_duration_s=duration,wall_duration_s=wall,
                 quality=['accumulated_recording'],count_rate_cps=total/duration if duration else None)
    snapshot=dict(spectrum=dict(counts=values,calibration_keV=list(next(iter(calibrations)))),
                  collection=metadata)
    return (actual_end,sorted(epochs),derived,snapshot),None


class AccumulatedSpectrum:
    """Incremental sums of complete, raw-verified recorded intervals.

    Epochs with different calibrations are never rebinned or silently combined.
    A requested UTC window includes only complete intervals within its bounds.
    """
    def __init__(self,run,start=None,end=None,epoch=None,cache_path=None):
        self.run=Path(run).resolve();self.start=start;self.end=end;self.epoch=epoch
        if start is not None and end is not None and start>=end:
            raise ValueError('Collection start must precede its end')
        manifest=(self.run/'manifest.json').read_bytes()
        self.config=json.loads(manifest.decode('utf-8'))
        self.policy=hashlib.sha256(json.dumps(dict(version=ACCUMULATION_CACHE_VERSION,
            run=str(self.run),manifest=hashlib.sha256(manifest).hexdigest(),
            start=start,end=end,epoch=epoch),sort_keys=True).encode()).hexdigest()
        self.cache_path=Path(cache_path) if cache_path else None
        self.cache_loaded=False;self.cached_restored=False
        self._reset()

    def _reset(self):
        self.last_seq=-1;self.previous=None;self.previous_epoch=0
        self.counts=None;self.calibration=None;self.intervals=0;self.device_duration=0.
        self.wall_duration=0.;self.actual_start=None;self.actual_end=None
        self.excluded_edges=0;self.boundaries=0;self.quality=Counter();self.epochs=set()
        self.head=-1;self.latest=None;self.last_file=None;self.last_sha256=None

    def restore_cache(self):
        """Load the last committed derived checkpoint without reading a run's raw data."""
        if self.cache_loaded:return self.cached_restored
        self.cache_loaded=True
        if not self.cache_path or not self.cache_path.exists():return False
        try:
            db=sqlite3.connect(self.cache_path.as_uri()+'?mode=ro',uri=True,timeout=.05)
            try:row=db.execute('SELECT policy,payload,payload_sha256 FROM checkpoint WHERE id=1').fetchone()
            finally:db.close()
            if (not row or row[0]!=self.policy
                    or hashlib.sha256(row[1].encode()).hexdigest()!=row[2]):return False
            state=json.loads(row[1])
            self.last_seq=state['last_seq'];self.previous=state['previous'];self.previous_epoch=state['previous_epoch']
            self.counts=np.asarray(state['counts'],dtype=np.int64) if state['counts'] is not None else None
            self.calibration=state['calibration'];self.intervals=state['intervals']
            self.device_duration=state['device_duration'];self.wall_duration=state['wall_duration']
            self.actual_start=state['actual_start'];self.actual_end=state['actual_end']
            self.excluded_edges=state['excluded_edges'];self.boundaries=state['boundaries']
            self.quality=Counter(state['quality']);self.epochs=set(state['epochs'])
            self.head=state['head'];self.latest=self.previous
            self.last_file=state['last_file'];self.last_sha256=state['last_sha256']
            self.cached_restored=self.last_seq>=0
            return self.cached_restored
        except (OSError,sqlite3.Error,ValueError,KeyError,TypeError):
            # Derived caches are disposable. Raw-file verification below remains
            # the authority if a cache cannot be read.
            self._reset()
            return False

    def validate_cached_endpoint(self):
        """Check the checkpoint's final raw snapshot before displaying it as cached."""
        if self.last_seq<0:return
        if self.previous is None or self.previous.get('seq')!=self.last_seq:
            raise ValueError('Accumulated-spectrum cache endpoint is malformed')
        db=readonly(self.run/'run.sqlite')
        try:
            head=db.execute('SELECT coalesce(max(seq),-1) FROM snapshots').fetchone()[0]
            row=db.execute('SELECT file,sha256 FROM snapshots WHERE seq=?',(self.last_seq,)).fetchone()
        finally:db.close()
        if head<self.last_seq or not row or row[0]!=self.last_file or row[1]!=self.last_sha256:
            raise ValueError(f'Accumulated-spectrum cache source endpoint changed at spectrum {self.last_seq}')
        raw_path=(self.run/row[0]).resolve()
        if not raw_path.is_relative_to(self.run):
            raise ValueError('Spectrum path escapes recording folder')
        raw=raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row[1]:
            raise ValueError(f'Accumulated-spectrum cache raw integrity mismatch at spectrum {self.last_seq}')

    def _save_cache(self):
        if not self.cache_path:return
        state=dict(last_seq=self.last_seq,previous=self.previous,previous_epoch=self.previous_epoch,
                   counts=self.counts.tolist() if self.counts is not None else None,
                   calibration=self.calibration,intervals=self.intervals,
                   device_duration=self.device_duration,wall_duration=self.wall_duration,
                   actual_start=self.actual_start,actual_end=self.actual_end,
                   excluded_edges=self.excluded_edges,boundaries=self.boundaries,
                   quality=dict(self.quality),epochs=sorted(self.epochs),head=self.head,
                   last_file=self.last_file,last_sha256=self.last_sha256)
        try:
            self.cache_path.parent.mkdir(parents=True,exist_ok=True)
            db=sqlite3.connect(self.cache_path,timeout=.25)
            try:
                db.execute('CREATE TABLE IF NOT EXISTS checkpoint(id INTEGER PRIMARY KEY CHECK(id=1),policy TEXT,payload TEXT,payload_sha256 TEXT)')
                payload=json.dumps(state,sort_keys=True,separators=(',',':'))
                digest=hashlib.sha256(payload.encode()).hexdigest()
                with db:db.execute('INSERT OR REPLACE INTO checkpoint VALUES(1,?,?,?)',
                                   (self.policy,payload,digest))
            finally:db.close()
        except (OSError,sqlite3.Error):
            # Cache failures must never stop analysis or affect recorded data.
            pass

    def update(self,limit=2000,cancelled=None):
        self.restore_cache()
        db=readonly(self.run/'run.sqlite')
        try:
            self.head=db.execute('SELECT coalesce(max(seq),-1) FROM snapshots').fetchone()[0]
            entries=list(db.execute('SELECT seq,file,sha256,epoch FROM snapshots WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?',
                                    (self.last_seq,self.head,limit)))
        finally:db.close()
        for seq,name,digest,stored_epoch in entries:
            if cancelled and cancelled():return False
            file=(self.run/name).resolve()
            if not file.is_relative_to(self.run):raise ValueError('Spectrum path escapes recording folder')
            raw=file.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError(f'Spectrum SHA-256 mismatch: {seq}')
            current=json.loads(raw)
            if current['seq']!=seq or seq!=self.last_seq+1:raise ValueError('Accumulated spectrum sequence mismatch')
            d=reconstruct(self.previous,current,self.config['interval_s'],self.previous_epoch)
            if d['epoch']!=stored_epoch:raise ValueError('Reconstruction epoch differs from the saved index')
            begin=midpoint(self.previous) if self.previous is not None else None
            finish=midpoint(current)
            selected=self.epoch is None or stored_epoch==self.epoch
            if selected and d['counts_delta'] is None:
                if (self.start is None or finish>=self.start) and (self.end is None or finish<=self.end):self.boundaries+=1
            elif selected:
                overlaps=(self.start is None or finish>self.start) and (self.end is None or begin<self.end)
                inside=(self.start is None or begin>=self.start) and (self.end is None or finish<=self.end)
                if overlaps and not inside:self.excluded_edges+=1
                if inside:
                    calibration=current['spectrum']['calibration_keV'];delta=d['counts_delta']
                    if self.counts is None:
                        self.counts=np.zeros(len(delta),dtype=np.int64);self.calibration=list(calibration)
                    if len(delta)!=len(self.counts) or list(calibration)!=self.calibration:
                        raise ValueError('Collection contains different calibrations/channel counts. Select one epoch; energy channels were not resampled.')
                    self.counts+=np.asarray(delta,dtype=np.int64)
                    self.intervals+=1;self.device_duration+=d['device_duration_s'];self.wall_duration+=d['wall_duration_s']
                    self.actual_start=begin if self.actual_start is None else min(self.actual_start,begin)
                    self.actual_end=finish if self.actual_end is None else max(self.actual_end,finish)
                    self.quality.update(d['quality']);self.epochs.add(stored_epoch)
            self.previous=current;self.previous_epoch=d['epoch'];self.last_seq=seq;self.latest=current
            self.last_file=name;self.last_sha256=digest
        self._save_cache()
        return self.last_seq>=self.head

    def result(self):
        if self.counts is None:return None
        counts=self.counts.tolist()
        metadata=dict(included_intervals=self.intervals,device_duration_s=self.device_duration,
                      actual_start_utc=self.actual_start,actual_end_utc=self.actual_end,
                      requested_start_utc=self.start,requested_end_utc=self.end,
                      excluded_boundary_intervals=self.excluded_edges,baseline_boundaries=self.boundaries,
                      quality_counts=dict(self.quality),epochs=sorted(self.epochs),through_seq=self.last_seq,
                      source_head=self.head,total_counts=sum(counts),
                      selection_rule='Complete recorded intervals only; no splitting, smoothing, resampling or pre-recording counts')
        d=dict(counts_delta=counts,device_duration_s=self.device_duration,wall_duration_s=self.wall_duration,
               quality=['accumulated_recording'] if self.start is None and self.end is None else ['accumulated_collection_range'],
               count_rate_cps=sum(counts)/self.device_duration if self.device_duration else None)
        snapshot=dict(spectrum=dict(counts=counts,calibration_keV=self.calibration),collection=metadata)
        return self.actual_end,sorted(self.epochs),d,snapshot


def latest_interval(run,epoch=None):
    run=Path(run).resolve()
    config=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    db=readonly(run/'run.sqlite')
    try:
        where=' WHERE epoch=?' if epoch is not None else ''
        entries=list(db.execute('SELECT seq,file,sha256,epoch FROM snapshots'+where+' ORDER BY seq DESC LIMIT 2',
                                (epoch,) if epoch is not None else ()))
    finally:db.close()
    if not entries:return None
    values=[]
    for seq,name,digest,record_epoch in reversed(entries):
        file=(run/name).resolve()
        if not file.is_relative_to(run):raise ValueError('Spectrum path escapes recording folder')
        raw=file.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError(f'Spectrum SHA-256 mismatch: {seq}')
        value=json.loads(raw)
        if value['seq']!=seq:raise ValueError('Spectrum sequence mismatch')
        values.append((value,record_epoch))
    current,current_epoch=values[-1]
    previous,previous_epoch=values[-2] if len(values)>1 else (None,current_epoch)
    derived=reconstruct(previous,current,config['interval_s'],previous_epoch)
    return midpoint(current),current_epoch,derived,current
