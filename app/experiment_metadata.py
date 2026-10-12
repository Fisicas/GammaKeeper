"""Versioned sample sheets and monitoring preferences in the annotation sidecar."""
import json
import math
from pathlib import Path
import annotations

FIELDS = [('sample_id','Sample ID'),('charcoal_type','Charcoal type'),('charcoal_mass_g','Charcoal mass (g)'),
          ('exposure_start_utc','Exposure start (ISO UTC)'),('exposure_end_utc','Exposure end (ISO UTC)'),
          ('exposure_location','Exposure location'),('canister','Canister ID'),('geometry','Counting geometry / photo path'),
          ('room_temperature_C','Room / canister temperature (°C; identify sensor)'),('relative_humidity_percent','Relative humidity (%)'),
          ('background_path','Linked background run folder'),('conditions','Conditions / changes')]


def latest(run, name):
    candidates=[n for n in annotations.load(run) if isinstance(n.get('metadata',{}).get(name),dict)]
    return max(candidates,key=lambda n:n['id']) if candidates else None


def save_sheet(run,utc,fields,time_basis='entered_by_user'):
    values={k:str(fields.get(k,'')).strip() for k,_ in FIELDS}
    for name in ('charcoal_mass_g','room_temperature_C','relative_humidity_percent'):
        if values[name]:
            number=float(values[name])
            if not math.isfinite(number) or (name=='charcoal_mass_g' and number<=0) or (name=='relative_humidity_percent' and not 0<=number<=100):
                raise ValueError('Invalid '+name)
            values[name]=number
    for name in ('exposure_start_utc','exposure_end_utc'):
        if values[name]:
            from analysis import timestamp
            timestamp(values[name])
    if values['exposure_start_utc'] and values['exposure_end_utc']:
        from analysis import timestamp
        if timestamp(values['exposure_end_utc'])<=timestamp(values['exposure_start_utc']):raise ValueError('Exposure end must follow start')
    if values['background_path']:
        from backup_run import digest
        background=Path(values['background_path']).resolve()
        if background==Path(run).resolve():raise ValueError('Sample cannot be its own background')
        config=json.loads((background/'manifest.json').read_text(encoding='utf-8'))
        values['background_path']=str(background)
        values['background_run_id']=background.name
        values['background_manifest_sha256']=digest(background/'manifest.json')
        values['background_label']=config.get('label','')
    return annotations.add(run,utc,'sample_sheet',values['sample_id'] or 'Sample sheet updated',time_basis,
                           metadata={'sample_sheet':values})


def monitoring(run):
    config=json.loads((Path(run)/'manifest.json').read_text(encoding='utf-8')).get('monitoring') or {}
    note=latest(run,'monitoring')
    if note:config=note['metadata']['monitoring']
    return dict(enabled=bool(config.get('enabled',False)),notifications=bool(config.get('notifications',True)),
                backup_parent=str(config.get('backup_parent') or ''),backup_interval_s=max(60.,float(config.get('backup_interval_s',86400))),
                disconnect_warn_s=max(30.,float(config.get('disconnect_warn_s',120))))
