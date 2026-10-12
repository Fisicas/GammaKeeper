"""Incremental live whole-run overview, using bounded coarser count/exposure bins."""
import hashlib
import json
from pathlib import Path
from experiment_engine import ExperimentEngine
from export_run import readonly
from journal_observations import JournalObservationIndex


def live_preview(run,head,config,epoch,max_bins,cache_dir,progress=None,cancelled=None):
    span=max(1,head)*config['interval_s']
    period=next((n for n in (config['interval_s'],60,600,3600,21600,86400)
                 if n>=config['interval_s'] and span/n<=max_bins),max(config['interval_s'],span/max_bins))
    key=hashlib.sha256((str(run)+repr((period,epoch))).encode()).hexdigest()
    cache_dir=Path(cache_dir);cache_dir.mkdir(parents=True,exist_ok=True)
    engine=ExperimentEngine(run,cache_dir/(key+'.sqlite'),period,epoch)
    try:
        cached=engine.db.execute("SELECT 1 FROM state WHERE key='engine'").fetchone() is not None
        while not engine.update(cancelled=cancelled):
            if cancelled and cancelled():
                engine.close();return None
            state=engine.result()
            if progress:progress(f"Loading verified overview: {state['source_head']:,}/{head:,}")
        result=engine.result()
    except BaseException:
        engine.close();raise
    rows=[]
    db=readonly(Path(run)/'run.sqlite')
    try:
        telemetry=JournalObservationIndex(db)
        engine.db.execute('CREATE TABLE IF NOT EXISTS telemetry_bins(key TEXT PRIMARY KEY,payload TEXT)')
        cache_safe=not any(result['summary']['quality_counts'].get(flag) for flag in ('host_clock_step','host_clock_step_during_read'))
        for row in result['rows']:
            d=dict(counts_delta=row['counts'].tolist(),device_duration_s=row['device_exposure_s'],
                   wall_duration_s=row['end_utc']-row['start_utc'],quality=row['quality'],
                   count_rate_cps=int(row['counts'].sum())/row['device_exposure_s'])
            signature=repr((row['first_seq'],row['last_seq'],row['start_utc'],row['end_utc']))
            stored=engine.db.execute('SELECT payload FROM telemetry_bins WHERE key=?',(signature,)).fetchone() if cache_safe else None
            tb=json.loads(stored[0]) if stored else telemetry.bin(row['start_utc'],row['end_utc'])
            if not stored and cache_safe and tb.get('finalized'):
                engine.db.execute('INSERT OR REPLACE INTO telemetry_bins VALUES(?,?)',(signature,json.dumps(tb)))
            saved=dict(spectrum=dict(counts=[0]*len(row['counts']),calibration_keV=row['calibration']),
                       telemetry_bin=tb,
                       display_first_seq=row['first_seq'],display_last_seq=row['last_seq'])
            rows.append((row['end_utc'],row['epoch'],d,saved))
        engine.db.commit()
    finally:db.close();engine.close()
    if len(rows)>max_bins*2+10:raise ValueError('Too many overview boundaries; select one epoch or a shorter view')
    result['summary']['epochs']=sorted(set(row[1] for row in rows))
    return dict(rows=rows,summary=result['summary'],head=result['source_head'],aggregation=max(1,round(period/config['interval_s'])),
                source=str(run),view='full',cached=bool(cached),signature=key,
                source_verified_utc=result['source_verified_utc'],note=result['cache_rule'])
