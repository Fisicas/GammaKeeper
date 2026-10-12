"""Bounded derived display aggregation; original samples and channels are retained."""
from collections import Counter
import hashlib
import json
import math
import gzip
import datetime as dt
import uuid
from pathlib import Path
from collections import deque
import numpy as np
from export_run import readonly
from telemetry_analysis import observation_from_batch,ObservationIndex
from analysis import timestamp,reconstruct


def read_preview(run, view='recent', epoch=None, max_bins=600, progress=None, cancelled=None, cache_dir=None):
    run=Path(run).resolve()
    config=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    db=readonly(run/'run.sqlite')
    try:
        head=db.execute('SELECT coalesce(max(seq),-1) FROM snapshots').fetchone()[0]
        if head<0:
            return dict(rows=[],summary={},head=head,aggregation=1)
        first=max(0,head-240) if view=='recent' else 0
        if isinstance(view,tuple):
            first,head=view
        cache_prefix=None
        terminal=db.execute("SELECT value FROM state WHERE key='recorder'").fetchone()
        closed=terminal and json.loads(terminal[0]).get('status') in ('completed','stopped')
        if cache_dir and not closed and view=='full':
            from incremental_preview import live_preview
            return live_preview(run,head,config,epoch,max_bins,cache_dir,progress,cancelled)
        signature=hashlib.sha256((str(run)+str((run/'run.sqlite').stat().st_mtime_ns)+
                                  (run/'manifest.json').read_text()+repr((first,head,epoch,max_bins))+
                                  hashlib.sha256(Path(__file__).read_bytes()).hexdigest()+
                                  hashlib.sha256(Path(__file__).with_name('analysis.py').read_bytes()).hexdigest()+
                                  hashlib.sha256(Path(__file__).with_name('telemetry_analysis.py').read_bytes()).hexdigest()).encode()).hexdigest()
        if cache_dir and closed:
            cache_dir=Path(cache_dir)
            cache_prefix=signature+'_'
            for file in sorted(cache_dir.glob(cache_prefix+'*.json.gz'),reverse=True) if cache_dir.exists() else []:
                try:
                    with gzip.open(file,'rt',encoding='utf-8') as handle:
                        cached=json.loads(handle.read(32*1024*1024))
                    if cached.get('signature')==signature:
                        cached['cached']=True
                        return cached
                except (OSError,ValueError,KeyError):
                    continue
        condition='seq>=? AND seq<=?'
        args=[first,head]
        if epoch is not None:
            condition+=' AND epoch=?';args.append(epoch)
        count=db.execute('SELECT count(*) FROM snapshots WHERE '+condition,args).fetchone()[0]
        factor=max(1,math.ceil(count/max_bins))
        group=[];rows=[];summary=dict(total_counts=0,device_elapsed_s=0.,snapshots=0,quality_counts={},epochs=[])
        epoch_set=set()
        previous_utc=None
        previous_raw=None
        previous_epoch=0
        zero_channels={}
        bounds=db.execute('SELECT min(utc),max(utc) FROM snapshots WHERE '+condition,args).fetchone()
        first_row=db.execute('SELECT seq,utc,derived FROM snapshots WHERE '+condition+' ORDER BY seq LIMIT 1',args).fetchone()
        observation_start=first_row[1]-(json.loads(first_row[2]).get('wall_duration_s') or 0.) if first_row else bounds[0]
        if first_row and first_row[0]>0:
            prior=db.execute('SELECT file,sha256,epoch FROM snapshots WHERE seq=?',(first_row[0]-1,)).fetchone()
            if prior:
                payload=(run/prior[0]).read_bytes()
                if hashlib.sha256(payload).hexdigest()!=prior[1]:
                    raise ValueError('Previous spectrum integrity mismatch')
                previous_raw=json.loads(payload)
                previous_epoch=prior[2]
        def observations():
            for payload, in db.execute('SELECT payload FROM telemetry WHERE utc>=? AND utc<=? ORDER BY utc,id',
                                        (min(bounds[0],observation_start)-3,bounds[1]+3)):
                observation=observation_from_batch(json.loads(payload))
                if observation:
                    yield observation
        observation_iterator=iter(observations())
        pending_observation=next(observation_iterator,None)
        observation_window=deque()
        def flush():
            nonlocal pending_observation
            if not group:
                return
            epoch_values={item[1] for item in group}
            quality=set(q for item in group for q in item[2]['quality'])
            usable=[item for item in group if item[2].get('counts_delta') is not None]
            calibration=group[-1][3]['spectrum']['calibration_keV']
            same_calibration=all(item[3]['spectrum']['calibration_keV']==calibration for item in group)
            delta=np.sum([item[2]['counts_delta'] for item in usable],axis=0).tolist() if usable and same_calibration else None
            duration=sum(item[2].get('device_duration_s') or 0 for item in usable)
            valid=len(usable)==len(group) and same_calibration and len(epoch_values)==1
            nominal=valid and quality=={'normal'}
            start=group[0][0]-(group[0][2].get('wall_duration_s') or 0.)
            end=group[-1][0]
            while pending_observation and timestamp(pending_observation['dt'])<=end+3:
                observation_window.append(pending_observation)
                pending_observation=next(observation_iterator,None)
            while observation_window and timestamp(observation_window[0]['dt'])<start-3:
                observation_window.popleft()
            tb=ObservationIndex(list(observation_window)).bin(start,end)
            s=dict(spectrum=dict(counts=delta or [0]*len(group[-1][3]['spectrum']['counts']),calibration_keV=calibration),
                   telemetry_bin=tb,
                   display_first_seq=group[0][3]['seq'],display_last_seq=group[-1][3]['seq'])
            d=dict(counts_delta=delta,device_duration_s=duration or None,wall_duration_s=end-start,
                   quality=['normal'] if nominal else sorted(quality-{'normal'}) or ['display_boundary'],
                   count_rate_cps=sum(delta)/duration if delta is not None and duration>0 else None)
            rows.append((end,group[-1][1],d,s))
            if len(rows)>max_bins*2+10:
                raise ValueError('Too many display boundaries. Select a shorter window or one epoch; raw data remain available.')
            group.clear()
        for number,(seq,utc,row_epoch,text) in enumerate(db.execute('SELECT seq,utc,epoch,derived FROM snapshots WHERE '+condition+' ORDER BY seq',args)):
            if cancelled and cancelled():
                return None
            d=json.loads(text)
            if group and row_epoch!=group[-1][1]:
                flush()
            if previous_utc is not None and utc<=previous_utc:
                raise ValueError('UTC reverses in this source window. Inspect sequence/clock flags in the export; the time axis was not reordered.')
            if group and d['quality']!=['normal']:
                flush()
            # Snapshot metadata comes from the exact file; validate its integrity.
            name,digest=db.execute('SELECT file,sha256 FROM snapshots WHERE seq=?',(seq,)).fetchone()
            raw=(run/name).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest:
                raise ValueError(f'Preview raw integrity mismatch: spectrum {seq}')
            saved=json.loads(raw)
            calculated=reconstruct(previous_raw,saved,config['interval_s'],
                                   previous_epoch)
            if calculated.get('counts_delta')!=d.get('counts_delta'):
                raise ValueError(f'Preview derived counts disagree with raw data: {seq}')
            d=calculated
            previous_epoch=calculated['epoch']
            previous_raw=saved
            nchan=len(saved['spectrum']['counts'])
            zero_channels.setdefault(nchan,[0]*nchan)
            saved=dict(seq=saved['seq'],spectrum=dict(counts=zero_channels[nchan],
                       calibration_keV=saved['spectrum']['calibration_keV']),telemetry_bin={})
            group.append((utc,row_epoch,d,saved))
            epoch_set.add(row_epoch)
            summary['snapshots']+=1
            if d.get('counts_delta') is not None:
                summary['total_counts']+=d['total_counts']
                summary['device_elapsed_s']+=d['device_duration_s']
            for flag in d['quality']:
                summary['quality_counts'][flag]=summary['quality_counts'].get(flag,0)+1
            if len(group)>=factor or d['quality']!=['normal']:
                flush()
            previous_utc=utc
            if progress and number%500==0:
                progress(f'Loading plot: {number+1:,}/{count:,} recorded spectra')
        flush()
        summary['epochs']=sorted(epoch_set)
        summary['mean_spectral_rate_cps']=summary['total_counts']/summary['device_elapsed_s'] if summary['device_elapsed_s'] else None
        result=dict(rows=rows,summary=summary,head=head,aggregation=factor,source=str(run),view=view,
                    cached=False,signature=signature,source_verified_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                    note='Display sums of recorded channel differences divided by summed device duration; mixed/gap groups left blank. Original channels retained.')
        if cache_prefix:
            try:
                cache_dir.mkdir(parents=True,exist_ok=True)
                file=cache_dir/(cache_prefix+uuid.uuid4().hex+'.json.gz')
                with file.open('xb') as stream:
                    with gzip.GzipFile(fileobj=stream,mode='wb') as compressed:
                        compressed.write(json.dumps(result).encode())
            except OSError:
                pass
        return result
    finally:
        db.close()
