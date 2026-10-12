
"""Read-only timing of v0.5.1 GUI data paths against saved runs. Writes only into this folder."""
import sys, os, time, json, hashlib
from pathlib import Path
app = Path(r'C:\Users\nelso\Desktop\Radiacode Data\radiacode_logger_v051')
sys.path.insert(0, str(app)); sys.path.insert(0, str(app/'dependencies'))
from preview import read_preview
from export_run import readonly
from telemetry_analysis import LiveJournalTelemetry
from analysis import reconstruct
root = app.parent
res = {}
def log(k,v):
    res[k]=v; (Path(__file__).parent/'benchmark_results_v01.json').write_text(json.dumps(res,indent=1))
run3 = root/'radiacode_logger_v03/runs/20261007_165726_535753_RC-103-006489'
run4 = root/'radiacode_logger_v04/runs/20261008_122840_752326_RC-103-006489'
t=time.perf_counter(); r=read_preview(run3,'recent',cache_dir=None); log('v03_recent_preview_s',time.perf_counter()-t)
t=time.perf_counter(); r=read_preview(run3,'full',cache_dir=None); log('v03_full_preview_uncached_s',time.perf_counter()-t); log('v03_full_rows',len(r['rows']))
t=time.perf_counter(); r=read_preview(run4,'full',cache_dir=None); log('v04_full_preview_uncached_s',time.perf_counter()-t)
# recover()-equivalent: read, hash, parse, reconstruct every raw spectrum (no DB writes)
cfg=json.loads((run4/'manifest.json').read_text()); prev=None; ep=0; n=0
t=time.perf_counter()
for f in sorted((run4/'spectra').glob('*/*.json'), key=lambda p:int(p.stem)):
    b=f.read_bytes(); hashlib.sha256(b).hexdigest(); v=json.loads(b); d=reconstruct(prev,v,cfg['interval_s'],ep); prev,ep=v,d['epoch']; n+=1
log('v04_resume_scan_equivalent_s',time.perf_counter()-t); log('v04_resume_scan_files',n)
db=readonly(run4/'run.sqlite'); first=db.execute('SELECT utc FROM snapshots ORDER BY seq LIMIT 1').fetchone()[0]
reader=LiveJournalTelemetry(first,2400); ticks=0; worst=0; t=time.perf_counter()
while True:
    t0=time.perf_counter(); done=reader.update(db); worst=max(worst,time.perf_counter()-t0); ticks+=1
    if done: break
log('v04_cold_telemetry_replay_s',time.perf_counter()-t); log('v04_replay_ticks',ticks); log('v04_replay_worst_tick_s',worst)
db.close(); log('done',True)
