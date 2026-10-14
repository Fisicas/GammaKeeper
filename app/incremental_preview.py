"""Incremental live whole-run overview, using bounded coarser count/exposure bins."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from experiment_engine import ExperimentEngine
from export_run import readonly
from journal_observations import JournalObservationIndex
from spectrum_view import accumulated_from_preview_rows


def _legacy_preview_caches(key,cache_dir):
    filename=key+'.sqlite'
    app_root=Path(__file__).resolve().parent
    candidates=[app_root/'cache'/'previews'/filename]
    configured=os.environ.get('GAMMAKEEPER_RELEASES')
    if configured:
        releases=Path(configured)
    elif (app_root.parent/'DEPLOYMENT.json').is_file():
        releases=app_root.parents[1]
    else:
        releases=app_root.parents[1]/'GammaKeeper Releases'
    current_app=app_root.resolve()
    try:
        release_dirs=list(releases.iterdir()) if releases.is_dir() else []
    except OSError:
        release_dirs=[]
    for release in release_dirs:
        if not release.is_dir():continue
        for app in (release/'app',release):
            if app.resolve()==current_app:continue
            candidates.append(app/'cache'/'previews'/filename)
    target=(Path(cache_dir)/filename).resolve()
    existing=[]
    for candidate in candidates:
        try:
            candidate=candidate.resolve()
            if candidate!=target and candidate.is_file():existing.append(candidate)
        except OSError:
            continue
    def modified(path):
        try:return path.stat().st_mtime_ns
        except OSError:return 0
    return sorted(set(existing),key=modified,reverse=True)


def _copy_legacy_preview_cache(target,candidates):
    """Copy one old per-release SQLite checkpoint without writing to that release."""
    target=Path(target)
    if target.exists():return False
    for source in candidates:
        if target.exists():return False
        temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.partial')
        source_db=destination_db=None
        try:
            source_db=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=.1)
            destination_db=sqlite3.connect(temporary,timeout=.25)
            source_db.backup(destination_db)
            destination_db.close();destination_db=None
            source_db.close();source_db=None
            # On Windows rename refuses to replace an existing checkpoint from
            # another GUI instance, so concurrent first opens remain harmless.
            os.rename(temporary,target)
            return True
        except (OSError,sqlite3.Error):
            continue
        finally:
            if destination_db is not None:destination_db.close()
            if source_db is not None:source_db.close()
            try:temporary.unlink()
            except OSError:pass
    return False


def _display_result(run,engine,result,requested_head,max_bins,period,cached,checkpoint=False):
    rows=[]
    db=readonly(Path(run)/'run.sqlite')
    try:
        telemetry=JournalObservationIndex(db)
        engine.db.execute('CREATE TABLE IF NOT EXISTS telemetry_bins(key TEXT PRIMARY KEY,payload TEXT)')
        cache_safe=not any(result['summary']['quality_counts'].get(flag)
                            for flag in ('host_clock_step','host_clock_step_during_read'))
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
                       telemetry_bin=tb,display_first_seq=row['first_seq'],display_last_seq=row['last_seq'],
                       display_interval_count=row['intervals'])
            rows.append((row['end_utc'],row['epoch'],d,saved))
        engine.db.commit()
    finally:
        db.close()
    if len(rows)>max_bins*2+10:
        raise ValueError('Too many overview boundaries; select one epoch or a shorter view')
    result['summary']['epochs']=sorted(set(row[1] for row in rows))
    accumulated,accumulated_error=accumulated_from_preview_rows(rows,result['source_head'],result['summary'])
    output=dict(rows=rows,summary=result['summary'],head=result['source_head'],
                aggregation=max(1,round(period/engine.config['interval_s'])),source=str(run),view='full',
                cached=bool(cached),signature=engine.policy,
                source_verified_utc=None if checkpoint else result['source_verified_utc'],
                verification_state='cached' if checkpoint else 'verified_current',
                requested_head=requested_head,
                note='Cached checkpoint is display-only while verification runs; every new raw interval is hash-checked. '
                     'Full integrity audit is separate.')
    output['accumulated_row']=accumulated
    output['accumulated_error']=accumulated_error
    return output


def live_preview(run,head,config,epoch,max_bins,cache_dir,progress=None,cancelled=None,snapshot=None):
    span=max(1,head)*config['interval_s']
    period=next((n for n in (config['interval_s'],60,600,3600,21600,86400)
                 if n>=config['interval_s'] and span/n<=max_bins),max(config['interval_s'],span/max_bins))
    key=hashlib.sha256((str(run)+repr((period,epoch))).encode()).hexdigest()
    cache_dir=Path(cache_dir);cache_dir.mkdir(parents=True,exist_ok=True)
    cache_file=cache_dir/(key+'.sqlite')
    if not cache_file.exists():
        _copy_legacy_preview_cache(cache_file,_legacy_preview_caches(key,cache_dir))
    engine=ExperimentEngine(run,cache_file,period,epoch)
    try:
        saved=engine.db.execute("SELECT payload FROM state WHERE key='engine'").fetchone()
        compatible=False
        if saved:
            try:
                compatible=json.loads(saved[0]).get('policy') in engine.compatible_policies
            except (TypeError,ValueError):
                compatible=False
        published=False
        if compatible and snapshot:
            cached_result=engine.result()
            if cached_result and cached_result['source_head']>=0:
                view=_display_result(run,engine,cached_result,head,max_bins,period,True,checkpoint=True)
                snapshot(view)
                published=True
        while not engine.update(cancelled=cancelled):
            if cancelled and cancelled():
                return None
            state=engine.result()
            if progress:
                progress(f"Loading verified overview: {state['source_head']:,}/{head:,}")
            # On a first open, publish after one bounded raw-verification batch;
            # subsequent cached opens publish the previous checkpoint immediately.
            if not published and snapshot and state['source_head']>=0:
                view=_display_result(run,engine,state,head,max_bins,period,False,checkpoint=True)
                view['verification_state']='partial'
                snapshot(view)
                published=True
        result=engine.result()
        return _display_result(run,engine,result,head,max_bins,period,compatible)
    finally:
        engine.close()
