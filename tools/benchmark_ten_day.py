"""Reproducible capacity benchmark: disposable synthetic spectra and telemetry only."""
import argparse
import ctypes
from ctypes import wintypes
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'app'
sys.path.insert(0,str(APP))
os.environ.update(GAMMAKEEPER_TEST_MODE='1',QT_QPA_PLATFORM='offscreen',GAMMAKEEPER_SCIENCE_PYTHON=sys.executable)
import bootstrap
from analysis import VERSION
from build_identity import identity
from recorder import make_config
from storage import RunStore,canonical
from experiment_engine import ExperimentEngine
from time_resolved import export_series
from scientific_qc import progress

BASE=dt.datetime(2026,1,1,tzinfo=dt.timezone.utc)
STAMP=BASE.timestamp()


def memory_mib():
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD),
                  *[(n,ctypes.c_size_t) for n in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage',
                    'PeakPagefileUsage','PrivateUsage')]]
    value=Counters();value.cb=ctypes.sizeof(value)
    process=ctypes.windll.kernel32.GetCurrentProcess
    process.restype=wintypes.HANDLE
    query=ctypes.windll.psapi.GetProcessMemoryInfo
    query.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    if not query(process(),ctypes.byref(value),value.cb):raise ctypes.WinError()
    return value.PeakWorkingSetSize/1024**2


def fixture(root,intervals,telemetry_step):
    run=root/'synthetic_recording'
    seconds=intervals*10
    config=make_config('SIMULATED',10,seconds,'demo','SYNTHETIC ten-day capacity; no actual specimen')
    config.update(created_utc=BASE.isoformat(),planned_end_utc=(BASE+dt.timedelta(seconds=seconds)).isoformat(),
                  synthetic_generator='Deterministic cumulative integer channel counts; fake one-second telemetry, not physical data')
    store=RunStore(run,config);store.close()
    db=sqlite3.connect(run/'run.sqlite');db.execute('PRAGMA synchronous=OFF')
    ch=np.arange(1024);edges=3*(np.arange(1025)-.5)
    from scipy.special import ndtr
    def gaussian(mean,sigma):
        return ndtr((edges[1:]-mean)/sigma)-ndtr((edges[:-1]-mean)/sigma)
    signal=2*gaussian(609.3,15)+1.5*gaussian(351.9,12)+.3*gaussian(1120.3,22)+.2*gaussian(1764.5,30)
    steady=.004+.7*gaussian(1460.8,30)+.08*gaussian(583.2,15)
    decay=math.log(2)/(3.8222*86400)
    previous=np.full(1024,100,dtype=np.int64);pending=[]
    started=time.perf_counter()
    for seq in range(intervals+1):
        t=seq*10;utc=(BASE+dt.timedelta(seconds=t)).isoformat()
        values=100+np.floor(steady*t+signal*(-np.expm1(-decay*t))/decay).astype(np.int64)
        snapshot=dict(seq=seq,schema=1,software_version=VERSION,serial='SIMULATED',boot_id='fixture',
                      connection_id='fixture',spectrum_generation=0,had_disconnect=False,
                      utc_before=utc,utc_after=utc,monotonic_before=float(t),monotonic_after=float(t),
                      read_time_halfwidth_s=0.,device_time_quantization_s=1.,
                      accumulated_dose=dict(dt=utc,temperature=22+2*math.sin(2*math.pi*t/86400),dose=1e-5),
                      spectrum=dict(counts=values.tolist(),duration_s=100+t,calibration_keV=[0.,3.,0.],raw_hex=None,format_version='SIMULATED'))
        flags=['normal'] if seq else ['baseline']
        delta=(values-previous).tolist() if seq else None
        d=dict(epoch=0,quality=flags,counts_delta=delta,count_rate_cps=sum(delta)/10 if delta else None,
               poisson_sigma_cps=math.sqrt(sum(delta))/10 if delta else None,
               device_duration_s=10 if seq else None,wall_duration_s=10 if seq else None)
        if seq:d['total_counts']=sum(delta)
        relative=f'spectra/{utc[:10]}/{seq:09d}.json';path=run/relative;path.parent.mkdir(parents=True,exist_ok=True)
        raw=canonical(snapshot)
        with path.open('xb') as handle:handle.write(raw)
        pending.append((seq,relative,hashlib.sha256(raw).hexdigest(),STAMP+t,0,canonical(d).decode()))
        if len(pending)>=1000:
            db.executemany('INSERT INTO snapshots VALUES(?,?,?,?,?,?)',pending);db.commit();pending.clear()
        if seq%10000==0:print(f'Fixture spectra {seq:,}/{intervals:,}',flush=True)
        previous=values
    db.executemany('INSERT INTO snapshots VALUES(?,?,?,?,?,?)',pending);db.commit()
    telemetry=[];hashes=[]
    for bid,t in enumerate(range(0,seconds+1,telemetry_step),1):
        utc=(BASE+dt.timedelta(seconds=t)).isoformat()
        batch=dict(utc_after=utc,connection_id='fixture',result=dict(raw_hex=None,decode_warning=None,
            records=[dict(type='RealTimeData',dt=utc,count_rate=float(steady.sum()+signal.sum()*math.exp(-decay*t)),dose_rate=1e-5)]))
        raw=canonical(batch);telemetry.append((bid,STAMP+t,raw.decode()));hashes.append((bid,hashlib.sha256(raw).hexdigest()))
        if len(telemetry)>=2000:
            db.executemany('INSERT INTO telemetry VALUES(?,?,?)',telemetry)
            db.executemany('INSERT INTO telemetry_integrity VALUES(?,?)',hashes);db.commit();telemetry.clear();hashes.clear()
        if bid%100000==0:print(f'Fixture telemetry {bid:,}',flush=True)
    db.executemany('INSERT INTO telemetry VALUES(?,?,?)',telemetry);db.executemany('INSERT INTO telemetry_integrity VALUES(?,?)',hashes)
    total=int((previous-100).sum())
    summary=dict(total_counts=total,device_elapsed_s=seconds,normal_device_exposure_s=seconds,
                 aggregate_device_exposure_s=0,intervals=intervals,snapshots=intervals+1,
                 mean_spectral_rate_cps=total/seconds,quality_counts={'normal':intervals,'baseline':1},
                 first_utc=BASE.isoformat(),last_utc=utc,epochs=1)
    status=dict(mode='demo',serial='SIMULATED',state='completed',pid=0,boot_id='fixture',
                utc=dt.datetime.now(dt.timezone.utc).isoformat(),interval_s=10,planned_end_utc=config['planned_end_utc'])
    for key,value in [('ui_status',status),('recorder',dict(status='completed',pid=0,boot_id='fixture')),('acquisition_summary',summary)]:
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',(key,json.dumps(value)))
    db.commit();db.close()
    import annotations
    from experiment_metadata import save_sheet
    annotations.add(run,STAMP,'seal','Synthetic benchmark seal; no actual canister','entered_by_user',metadata={'synthetic':True})
    save_sheet(run,STAMP,dict(sample_id='SYNTHETIC FIXTURE',charcoal_type='no actual material',geometry='Synthetic fixed fixture',
                             conditions='Capacity benchmark only; deterministic spectra and fake telemetry'))
    return run,previous-100,time.perf_counter()-started,bid


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--intervals',type=int,default=86400)
    parser.add_argument('--telemetry-step',type=int,default=1)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--screenshot',type=Path)
    args=parser.parse_args()
    if args.intervals<1 or args.telemetry_step<1:parser.error('Positive sizes required')
    if args.output.exists():raise FileExistsError(args.output)
    parent=APP/'validation'/'capacity_temp';parent.mkdir(exist_ok=True)
    if shutil.disk_usage(parent).free<8*1024**3:raise RuntimeError('Capacity benchmark needs at least 8 GiB free')
    metrics=dict(app_version=VERSION,analysis_build=identity(),intervals=args.intervals,channels=1024,
                 telemetry_step_s=args.telemetry_step,synthetic=True,hardware_access=False,
                 timing_scope='Fresh derived cache and warm filesystem; OS cold-cache behavior not qualified')
    with tempfile.TemporaryDirectory(prefix='ten_day_',dir=parent) as folder:
        root=Path(folder).resolve()
        assert root.is_relative_to(parent.resolve())
        run,expected,prep,telemetry=fixture(root,args.intervals,args.telemetry_step)
        metrics.update(preparation_s=prep,telemetry_batches=telemetry)
        print('Measuring integrity recovery',flush=True)
        started=time.perf_counter();store=RunStore(run);store.close()
        metrics['integrity_recovery_s']=time.perf_counter()-started
        print('Measuring fresh scientific cache',flush=True)
        started=time.perf_counter();engine=ExperimentEngine(run,root/'overview.sqlite')
        while not engine.update():pass
        result=engine.result();engine.close()
        metrics['fresh_count_overview_s']=time.perf_counter()-started
        np.testing.assert_array_equal(sum((r['counts'] for r in result['rows'])),expected)
        started=time.perf_counter();engine=ExperimentEngine(run,root/'overview.sqlite');engine.update();engine.result();engine.close()
        metrics['warm_count_overview_s']=time.perf_counter()-started
        print('Measuring native streamed export',flush=True)
        started=time.perf_counter();target,report=export_series(run,root/'export',chunk_size=256)
        metrics['native_export_s']=time.perf_counter()-started
        metrics['native_export_peak_working_set_MiB']=memory_mib()
        output=np.zeros(1024,dtype=np.int64)
        for item in report['segments']:
            with np.load(target/(item['stem']+'.npz')) as arrays:
                output+=arrays['counts'].sum(axis=0)
                assert len(arrays['counts'])<=256
        np.testing.assert_array_equal(output,expected)
        metrics.update(export_chunks=len(report['segments']),count_conservation_passed=True,
                       device_exposure_s=args.intervals*10)
        print('Measuring full live preview cache',flush=True)
        from incremental_preview import live_preview
        config=json.loads((run/'manifest.json').read_text())
        started=time.perf_counter();live_preview(run,args.intervals,config,None,600,root/'previews')
        metrics['fresh_live_preview_s']=time.perf_counter()-started
        started=time.perf_counter();live_preview(run,args.intervals,config,None,600,root/'previews')
        metrics['warm_live_preview_s']=time.perf_counter()-started
        if args.screenshot:
            print('Rendering synthetic GUI',flush=True)
            from PySide6 import QtWidgets as Q,QtGui,QtCore
            from gui import Window
            app=Q.QApplication.instance() or Q.QApplication([])
            for name in ('segoeui.ttf','segoeuib.ttf','seguisb.ttf'):
                file=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'/name
                if file.exists():QtGui.QFontDatabase.addApplicationFontFromData(QtCore.QByteArray(file.read_bytes()))
            app.setFont(QtGui.QFont('Segoe UI',10))
            qc=progress(run)
            window=Window(str(run),read_only=True)
            try:
                window.timer.stop();window.notes_timer.stop();window.science_result=qc;window.draw_science()
                window.tabs.blockSignals(True);window.tabs.setCurrentWidget(window.experiment_page);window.tabs.blockSignals(False)
                window.resize(1500,1080);window.show();app.processEvents();window.science_canvas.draw();app.processEvents()
                if not window.grab().save(str(args.screenshot)):raise RuntimeError('Screenshot failed')
            finally:
                window._closing=True;window.preview_cancel.set();window.accumulation_cancel.set();window.science_cancel.set()
                window.preview_pool.waitForDone(30000);window.accumulation_pool.waitForDone(30000);window.science_pool.waitForDone(30000)
                window.close();app.processEvents()
        assert root.is_relative_to(parent.resolve())
    with args.output.open('x',encoding='utf-8') as handle:json.dump(metrics,handle,indent=2)
    print(json.dumps(metrics,indent=2),flush=True)


if __name__=='__main__':main()
