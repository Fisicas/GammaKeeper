"""Incremental, hash-checked count/exposure sums for scientific QC and live overviews."""
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import numpy as np
from analysis import reconstruct, midpoint
from export_run import readonly

AGGREGATE = {'gap_recovered_aggregate','reconnect_aggregate_continuity_inferred'}
# Increment only when the persisted overview aggregation semantics change.
ENGINE_CACHE_VERSION = 1
# The deployed 0.7.2 build used the complete application source digest in its
# cache policy. Keep those checkpoints reusable after checking their endpoint.
COMPATIBLE_CACHE_SOURCE_SHA256 = (
    '07e34f9a649229247a5bbbf2ec424b9c9eb7d6a32f821aa6a27ca9298f96e374',
)


class ExperimentEngine:
    def __init__(self,run,cache=None,period_s=3600,epoch=None):
        self.run=Path(run).resolve()
        self.config=json.loads((self.run/'manifest.json').read_text(encoding='utf-8'))
        self.period=period_s
        self.epoch=epoch
        self.db=sqlite3.connect(str(cache) if cache else ':memory:',timeout=10)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,payload TEXT);
          CREATE TABLE IF NOT EXISTS bins(key TEXT PRIMARY KEY,counts BLOB,payload TEXT);
          CREATE TABLE IF NOT EXISTS fits(key TEXT PRIMARY KEY,payload TEXT);
        ''')
        policy_inputs=dict(version=ENGINE_CACHE_VERSION,run=str(self.run),config=self.config,
                           period_s=period_s,epoch=epoch)
        self.policy=hashlib.sha256(json.dumps(policy_inputs,sort_keys=True).encode()).hexdigest()
        legacy_prefix=(str(self.run)+json.dumps(self.config,sort_keys=True)+repr((period_s,epoch)))
        self.compatible_policies={self.policy}
        self.compatible_policies.update(hashlib.sha256((legacy_prefix+source_hash).encode()).hexdigest()
                                        for source_hash in COMPATIBLE_CACHE_SOURCE_SHA256)

    def close(self): self.db.close()

    def update(self,limit=2000,cancelled=None):
        source=readonly(self.run/'run.sqlite')
        try:
            head=source.execute('SELECT coalesce(max(seq),-1) FROM snapshots').fetchone()[0]
            self.db.execute('BEGIN IMMEDIATE')
            saved=self.db.execute("SELECT payload FROM state WHERE key='engine'").fetchone()
            state=json.loads(saved[0]) if saved else None
            if not state or state['policy'] not in self.compatible_policies:
                self.db.execute('DELETE FROM bins');self.db.execute('DELETE FROM fits')
                if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='telemetry_bins'").fetchone():
                    self.db.execute('DELETE FROM telemetry_bins')
                state=dict(policy=self.policy,last_seq=-1,epoch=0,previous=None,segment=0,source_head=head,
                           summary=dict(total_counts=0,device_elapsed_s=0.,normal_device_exposure_s=0.,
                                        aggregate_device_exposure_s=0.,invalid_wall_s=0.,quality_counts={},snapshots=0))
            else:
                # Promote the validated cache identity after the normal endpoint
                # hash check below; do not discard otherwise-compatible 0.7.2 bins.
                state['policy']=self.policy
            summary=state['summary']
            if state['last_seq']>head: raise ValueError('Derived cache extends beyond source')
            if state.get('last_file'):
                endpoint=(self.run/state['last_file']).resolve()
                if not endpoint.is_relative_to(self.run): raise ValueError('Cached raw path escapes recording')
                raw=endpoint.read_bytes()
                indexed=source.execute('SELECT sha256 FROM snapshots WHERE seq=?',(state['last_seq'],)).fetchone()
                if not indexed or indexed[0]!=state['last_sha256'] or hashlib.sha256(raw).hexdigest()!=indexed[0]:
                    raise ValueError('Cached source endpoint integrity mismatch; rebuild/audit required')
            entries=source.execute('SELECT seq,file,sha256,epoch FROM snapshots WHERE seq>? AND seq<=? ORDER BY seq LIMIT ?',
                                   (state['last_seq'],head,limit))
            for seq,name,digest,ep in entries:
                if cancelled and cancelled(): break
                if seq!=state['last_seq']+1: raise ValueError('Missing raw snapshot sequence')
                path=(self.run/name).resolve()
                if not path.is_relative_to(self.run): raise ValueError('Raw path escapes recording')
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('Raw spectrum integrity mismatch: '+name)
                current=json.loads(raw)
                if current['seq']!=seq: raise ValueError('Raw sequence differs from index')
                d=reconstruct(state['previous'],current,self.config['interval_s'],state['epoch'])
                if d['epoch']!=ep: raise ValueError('Derived epoch differs from raw reconstruction')
                flags=set(d['quality'])
                begin=midpoint(state['previous']) if state['previous'] else None
                finish=midpoint(current)
                summary['snapshots']+=1
                for flag in flags: summary['quality_counts'][flag]=summary['quality_counts'].get(flag,0)+1
                summary['first_utc']=summary.get('first_utc',finish);summary['last_utc']=finish
                if d['counts_delta'] is not None:
                    summary['total_counts']+=d['total_counts'];summary['device_elapsed_s']+=d['device_duration_s']
                    field='aggregate_device_exposure_s' if flags & AGGREGATE else 'normal_device_exposure_s'
                    summary[field]+=d['device_duration_s']
                    if self.epoch is None or self.epoch==ep:
                        if flags-{'normal','final_short_interval'}: state['segment']+=1
                        key=repr((ep,int(begin//self.period),state['segment']))
                        row=self.db.execute('SELECT counts,payload FROM bins WHERE key=?',(key,)).fetchone()
                        if row:
                            counts=np.frombuffer(row[0],dtype='<i8').copy();meta=json.loads(row[1])
                        else:
                            counts=np.zeros(len(d['counts_delta']),dtype=np.int64)
                            meta=dict(epoch=ep,calibration=current['spectrum']['calibration_keV'],start_utc=begin,
                                      end_utc=finish,device_exposure_s=0.,intervals=0,quality=[],temperatures=[],
                                      temperature_record_id=None,temperature_age_max_s=None,first_seq=seq,last_seq=seq)
                        counts+=np.asarray(d['counts_delta'],dtype=np.int64)
                        meta['end_utc']=finish;meta['device_exposure_s']+=d['device_duration_s'];meta['intervals']+=1
                        meta['last_seq']=seq;meta['quality']=sorted(set(meta['quality'])|flags)
                        rare=current.get('accumulated_dose') or {}
                        if rare.get('temperature') is not None and rare.get('dt')!=meta['temperature_record_id']:
                            temp=float(rare['temperature'])
                            if np.isfinite(temp):
                                meta['temperatures'].append(temp)
                                # Retain moments instead of an unbounded list of native temperatures.
                                old=meta.get('temperature_stats',dict(n=0,sum=0.,min=temp,max=temp))
                                old.update(n=old['n']+1,sum=old['sum']+temp,min=min(old['min'],temp),max=max(old['max'],temp))
                                meta['temperature_stats']=old
                                meta['temperature_record_id']=rare.get('dt')
                                try:
                                    from analysis import timestamp
                                    age=abs(finish-timestamp(rare['dt']))
                                    meta['temperature_age_max_s']=max(meta.get('temperature_age_max_s') or 0.,age)
                                except (TypeError,ValueError): pass
                        meta['temperatures']=[]
                        self.db.execute('INSERT OR REPLACE INTO bins VALUES(?,?,?)',(key,counts.astype('<i8').tobytes(),json.dumps(meta)))
                        if flags-{'normal','final_short_interval'}: state['segment']+=1
                else:
                    summary['invalid_wall_s']+=max(0.,d.get('wall_duration_s') or 0.) if begin is not None else 0.
                    state['segment']+=1
                state.update(previous=current,last_seq=seq,epoch=ep,last_file=name,last_sha256=digest)
            state['source_head']=head
            summary['mean_spectral_rate_cps']=summary['total_counts']/summary['device_elapsed_s'] if summary['device_elapsed_s'] else None
            self.db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('engine',json.dumps(state)))
            self.db.commit()
            return state['last_seq']>=head
        except BaseException:
            self.db.rollback();raise
        finally:source.close()

    def result(self,with_peaks=False,cancelled=None):
        from scientific_models import WINDOWS, peak_fit
        state=json.loads(self.db.execute("SELECT payload FROM state WHERE key='engine'").fetchone()[0])
        rows=[]
        for key,raw,payload in self.db.execute('SELECT key,counts,payload FROM bins ORDER BY rowid'):
            if cancelled and cancelled():return None
            counts=np.frombuffer(raw,dtype='<i8').copy();meta=json.loads(payload)
            ch=np.arange(len(counts));a0,a1,a2=meta['calibration'];energy=a0+a1*ch+a2*ch**2
            if np.any(np.diff(energy)<=0): raise ValueError('Nonmonotonic calibration; energy was not reordered')
            meta['window_counts']={name:int(counts[(energy>=low)&(energy<=high)&(ch<len(ch)-1)].sum())
                                   for name,low,high in WINDOWS}
            if with_peaks:
                fingerprint=key+'|'+hashlib.sha256(raw).hexdigest()
                saved=self.db.execute('SELECT payload FROM fits WHERE key=?',(fingerprint,)).fetchone()
                if saved: meta['peaks']=json.loads(saved[0])
                else:
                    meta['peaks']={name:peak_fit(counts,meta['calibration'],reference,low,high,blend)
                        for name,reference,low,high,blend in [('609 region',609.3,550,680,583.2),('K-40',1460.8,1320,1600,None)]}
                    self.db.execute('INSERT OR REPLACE INTO fits VALUES(?,?)',(fingerprint,json.dumps(meta['peaks'])))
            stats=meta.get('temperature_stats')
            meta['temperature_C']=stats['sum']/stats['n'] if stats else None
            meta['temperature_quality']='stale_or_projected_timestamp' if (meta.get('temperature_age_max_s') or 0)>300 else 'device_status' if stats else 'not_available'
            meta['counts']=counts
            rows.append(meta)
        if with_peaks:self.db.commit()
        return dict(rows=rows,summary=state['summary'],source_head=state['last_seq'],
                    source_verified_utc=dt.datetime.now(dt.timezone.utc).isoformat(),period_s=self.period,
                    cache_rule='New intervals and cached endpoint hash checked; cached prefix retains earlier verification. Full raw audit is separate.',
                    processing='Integer native-channel sums, actual device exposure; no smoothing, resampling or raw background subtraction')
