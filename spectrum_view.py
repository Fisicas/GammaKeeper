"""A raw-verified latest recording interval, independent of heatmap selection."""
import hashlib
import json
from pathlib import Path
from analysis import reconstruct,midpoint
from export_run import readonly
import numpy as np
from collections import Counter


class AccumulatedSpectrum:
    """Incremental sums of complete, raw-verified recorded intervals.

    Epochs with different calibrations are never rebinned or silently combined.
    A requested UTC window includes only complete intervals within its bounds.
    """
    def __init__(self,run,start=None,end=None,epoch=None):
        self.run=Path(run).resolve();self.start=start;self.end=end;self.epoch=epoch
        if start is not None and end is not None and start>=end:
            raise ValueError('Collection start must precede its end')
        self.config=json.loads((self.run/'manifest.json').read_text(encoding='utf-8'))
        self.last_seq=-1;self.previous=None;self.previous_epoch=0
        self.counts=None;self.calibration=None;self.intervals=0;self.device_duration=0.
        self.wall_duration=0.;self.actual_start=None;self.actual_end=None
        self.excluded_edges=0;self.boundaries=0;self.quality=Counter();self.epochs=set()
        self.head=-1;self.latest=None

    def update(self,limit=2000,cancelled=None):
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
