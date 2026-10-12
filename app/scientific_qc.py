"""Read-only scientific progress and versioned QC exports; no absolute activity calculation."""
import bootstrap
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import numpy as np
import annotations
from backup_run import digest
from experiment_engine import ExperimentEngine
from experiment_metadata import latest
from scientific_models import WINDOWS, decay_fit
from storage import immutable_json
from build_identity import identity


def progress(run,cache_root=None,ingrowth_exclusion_h=3.,cancelled=None):
    run=Path(run).resolve()
    notes=annotations.load(run);seal=annotations.seal(notes)
    sheet=latest(run,'sample_sheet')
    def read(source,peaks):
        cache=None
        if cache_root:
            folder=Path(cache_root);folder.mkdir(parents=True,exist_ok=True)
            cache=folder/(hashlib.sha256(str(source).encode()).hexdigest()+'.sqlite')
        engine=ExperimentEngine(source,cache)
        try:
            while not engine.update(cancelled=cancelled):
                if cancelled and cancelled():return None
            return engine.result(with_peaks=peaks,cancelled=cancelled)
        finally:engine.close()
    result=read(run,True)
    if result is None:return None
    result.update(notes=notes,seal_note=seal,sample_sheet=sheet,analysis_build=identity(),background=None,
                  ingrowth_exclusion_h=ingrowth_exclusion_h,decay_fits={})
    result['source_manifest']=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    result['source_manifest_sha256']=digest(run/'manifest.json')
    result['simulated']=result['source_manifest'].get('mode')=='demo'
    result['window_definitions']=[dict(name=name,energy_min_keV=low,energy_max_keV=high,
        inclusion='Inclusive channel centers; terminal channel excluded; no channel splitting') for name,low,high in WINDOWS]
    geometries={n['metadata']['sample_sheet'].get('geometry') for n in notes if isinstance(n.get('metadata',{}).get('sample_sheet'),dict) and n['metadata']['sample_sheet'].get('geometry')}
    result['provenance_warnings']=[]
    if len(geometries)>1:result['provenance_warnings'].append('Geometry descriptions differ between sample versions; verify correction versus physical change')
    if sheet and sheet['metadata']['sample_sheet'].get('background_path'):
        values=sheet['metadata']['sample_sheet'];source=Path(values['background_path'])
        if digest(source/'manifest.json')!=values['background_manifest_sha256']:raise ValueError('Linked background manifest changed')
        bg=read(source,False)
        if bg is None:return None
        counts={name:sum(row['window_counts'][name] for row in bg['rows']) for name,_,_ in WINDOWS}
        exposure=sum(row['device_exposure_s'] for row in bg['rows'])
        bg_sheet=latest(source,'sample_sheet')
        bg_values=bg_sheet['metadata']['sample_sheet'] if bg_sheet else {}
        geometry_matches=bool(values.get('geometry') and values.get('geometry')==bg_values.get('geometry'))
        result['background']=dict(run_id=source.name,manifest_sha256=values['background_manifest_sha256'],
                                  source_head=bg['source_head'],window_counts=counts,device_exposure_s=exposure,
                                  geometry_documented_match=geometry_matches,
                                  assumption='Constant background rate in unchanged geometry; not established by linkage')
        if exposure>0:
            for row in result['rows']:
                t=row['device_exposure_s']
                row['net_rate_cps']={name:row['window_counts'][name]/t-counts[name]/exposure for name,_,_ in WINDOWS}
                row['net_poisson_sigma_cps']={name:float(np.sqrt(row['window_counts'][name]/t**2+counts[name]/exposure**2)) for name,_,_ in WINDOWS}
                row['shared_background_variance_cps2']={name:counts[name]/exposure**2 for name,_,_ in WINDOWS}
    if seal:
        for window in ('Pb-214 352 region','609 / 583 region'):
            bg=result['background']
            evidence=(bg['window_counts'][window],bg['device_exposure_s']) if bg and bg['device_exposure_s']>0 else None
            fit=decay_fit(result['rows'],window,seal['utc'],ingrowth_exclusion_h,evidence)
            if bg and not bg['geometry_documented_match']:
                fit.update(status='geometry_requires_review',reason='Matching sample/background geometry not documented')
            if len(geometries)>1:
                fit.update(status='geometry_requires_review',reason=result['provenance_warnings'][0])
            result['decay_fits'][window]=fit
    for name in ('609 region','K-40'):
        reference=None
        for row in result['rows']:
            fit=row.get('peaks',{}).get(name,{})
            if fit.get('status')=='qualified_advisory':
                if reference is None:reference=fit['centroid_keV']
                fit['relative_drift_keV']=fit['centroid_keV']-reference
                fit['drift_attention']=abs(fit['relative_drift_keV'])>5.
                fit['drift_threshold_keV']=5.
    return result


def export_qc(run,target,ingrowth_exclusion_h=3.,cache_root=None):
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=False)
    result=progress(run,cache_root=cache_root,ingrowth_exclusion_h=ingrowth_exclusion_h)
    for row in result['rows']:row['counts']=row['counts'].tolist()
    immutable_json(target/'SCIENTIFIC_QC.json',result)
    immutable_json(target/'COMPLETE.json',dict(analysis_build=identity(),source_run=str(Path(run).resolve()),
        source_head=result['source_head'],file_sha256=digest(target/'SCIENTIFIC_QC.json'),
        qualification='Advisory QC only; no absolute activity or hardware/sample qualification',
        completed_utc=dt.datetime.now(dt.timezone.utc).isoformat()))
    return target


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run',type=Path);parser.add_argument('--target',required=True,type=Path)
    parser.add_argument('--ingrowth-exclusion-hours',type=float,default=3.)
    parser.add_argument('--cache-root',type=Path)
    args=parser.parse_args()
    if not np.isfinite(args.ingrowth_exclusion_hours) or args.ingrowth_exclusion_hours<0:parser.error('Invalid ingrowth exclusion')
    print(export_qc(args.run,args.target,args.ingrowth_exclusion_hours,args.cache_root))
