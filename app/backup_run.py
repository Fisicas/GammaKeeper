"""Resumable, verified local snapshot backups; never overwrite a destination file."""
import bootstrap
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid
from export_run import readonly
from storage import immutable_json


def digest(file):
    h=hashlib.sha256()
    with Path(file).open('rb') as handle:
        for block in iter(lambda:handle.read(1024*1024),b''):
            h.update(block)
    return h.hexdigest()


def backup(run,target,progress=None):
    run,target=Path(run).resolve(),Path(target).resolve()
    if target==run or target.is_relative_to(run):
        raise ValueError('Choose a backup location outside the source run')
    manifest_hash=digest(run/'manifest.json')
    plan_file=target/'BACKUP_PLAN.json'
    if target.exists():
        if not plan_file.exists():
            raise ValueError('Destination exists without a backup plan; refusing to modify it')
        plan=json.loads(plan_file.read_text(encoding='utf-8'))
        if plan.get('source_run')!=str(run) or plan.get('manifest_sha256')!=manifest_hash:
            raise ValueError('Backup belongs to a different source or changed manifest')
    else:
        target.mkdir(parents=True,exist_ok=False)
        plan=dict(schema=1,source_run=str(run),manifest_sha256=manifest_hash,
                  staging_name='run.sqlite.'+uuid.uuid4().hex+'.partial',
                  created_utc=dt.datetime.now(dt.timezone.utc).isoformat())
        immutable_json(plan_file,plan)
    index=target/'run.sqlite'
    if not index.exists():
        name=plan['staging_name']
        if not re.fullmatch(r'run\.sqlite\.[0-9a-f]{32}\.partial',name):
            raise ValueError('Invalid backup staging name')
        stage=target/name
        if stage.exists():
            stage.unlink()  # Only this plan's known unfinished database snapshot.
        source=readonly(run/'run.sqlite')
        destination=sqlite3.connect(stage)
        try:
            source.backup(destination,pages=256,sleep=.01)
        finally:
            destination.close();source.close()
        os.rename(stage,index)
    checkpoint_file=target/'BACKUP_CHECKPOINT.json'
    if checkpoint_file.exists():
        checkpoint=json.loads(checkpoint_file.read_text(encoding='utf-8'))
        if checkpoint['index_sha256']!=digest(index):
            raise ValueError('Backup journal changed after checkpoint')
    else:
        db=readonly(index)
        try:
            if [r[0] for r in db.execute('PRAGMA integrity_check')]!=['ok']:
                raise ValueError('Backup journal integrity failed')
            files=[dict(path=name,sha256=sha) for name,sha in db.execute('SELECT file,sha256 FROM snapshots ORDER BY seq')]
            snapshot_count=len(files)
        finally:
            db.close()
        files.append(dict(path='manifest.json',sha256=manifest_hash))
        files.extend(dict(path=file.relative_to(run).as_posix(),sha256=digest(file))
                     for file in sorted((run/'connections').glob('*.json')))
        checkpoint=dict(schema=1,index_sha256=digest(index),snapshots=snapshot_count,files=files)
        immutable_json(checkpoint_file,checkpoint)
    for number,item in enumerate(checkpoint['files']):
        source=(run/item['path']).resolve()
        destination=(target/item['path']).resolve()
        if not source.is_relative_to(run) or not destination.is_relative_to(target):
            raise ValueError('Backup file path escapes its source/destination')
        if destination.exists():
            if digest(destination)!=item['sha256']:
                raise ValueError(f'Existing backup file differs; not overwritten: {item["path"]}')
        else:
            if digest(source)!=item['sha256']:
                raise ValueError(f'Source file integrity failed: {item["path"]}')
            destination.parent.mkdir(parents=True,exist_ok=True)
            stage=destination.with_name(destination.name+'.'+item['sha256'][:12]+'.partial')
            if stage.exists():
                stage.unlink()  # This plan's unfinished copy, never a committed output.
            shutil.copy2(source,stage)
            if digest(stage)!=item['sha256']:
                raise ValueError('Copied file hash mismatch')
            os.rename(stage,destination)
        if progress:
            progress(number+1,len(checkpoint['files']))
    result=dict(schema=1,verified=True,source_run=str(run),snapshots=checkpoint['snapshots'],
                index_sha256=checkpoint['index_sha256'],source_manifest_sha256=manifest_hash,
                completed_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                note='Read-only point-in-time backup. Resume copying reuses this journal horizon; recording from the backup is disabled.')
    marker=target/'BACKUP_COMPLETE.json'
    if not marker.exists():
        immutable_json(marker,result)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('run',type=Path)
    parser.add_argument('target',type=Path)
    args=parser.parse_args()
    print(json.dumps(backup(args.run,args.target),indent=2))
