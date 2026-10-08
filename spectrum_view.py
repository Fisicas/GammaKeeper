"""A raw-verified latest recording interval, independent of heatmap selection."""
import hashlib
import json
from pathlib import Path
from analysis import reconstruct,midpoint
from export_run import readonly


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
