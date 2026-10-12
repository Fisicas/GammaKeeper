"""Read-only disk estimate from the journal and indexed spectrum sizes; no tree scan."""
from pathlib import Path
import shutil
from analysis import timestamp
from storage import canonical


def disk_budget(run,state,config,now):
    run=Path(run)
    journal=sum(p.stat().st_size for p in (run/'run.sqlite',run/'run.sqlite-wal') if p.exists())
    snapshots=state.get('snapshots',0)
    raw_size=len(canonical(state['last_snapshot'])) if state.get('last_snapshot') else 0
    estimate=journal+snapshots*raw_size
    first=(state.get('acquisition_summary') or {}).get('first_utc')
    if isinstance(first,str):first=timestamp(first)
    elapsed=max(0.,now-first) if first is not None else 0.
    end=timestamp(config['planned_end_utc']) if config.get('planned_end_utc') else None
    remaining=max(0.,end-now) if end is not None else None
    growth=estimate/elapsed if elapsed>=60 and estimate else None
    free=shutil.disk_usage(run).free
    needed=growth*remaining if growth is not None and remaining is not None else None
    return dict(free_bytes=free,estimated_recording_bytes=estimate,estimated_bytes_per_hour=growth*3600 if growth else None,
                estimated_remaining_bytes=needed,headroom_warning=needed is not None and free<needed+1024**3,
                method='Journal size plus latest raw spectrum size times snapshot count; approximate, not a full file census',
                reserve_note='Estimate excludes exports and backups; reserve additional destination space')
