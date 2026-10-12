"""Disk-backed time-resolved export with bounded spectral-array memory."""
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import numpy as np
import annotations
from analysis import reconstruct, midpoint, timestamp, VERSION
from build_identity import identity
from export_run import readonly
from storage import immutable_json
from time_resolved import aligned_count, rcspg_payload, read_rcspg, iso

ALLOWED = {'normal', 'final_short_interval'}
AGGREGATE = {'gap_recovered_aggregate', 'reconnect_aggregate_continuity_inferred'}


def stage_source(run, db, start, end, epoch, keep_aggregates):
    config = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    db.executescript('''
      CREATE TABLE bins(id INTEGER PRIMARY KEY, segment INTEGER, seq INTEGER, epoch INTEGER,
        begin REAL, end REAL, exposure REAL, total INTEGER, calibration TEXT, flags TEXT,
        clock_quality TEXT, uncertainty REAL, halfwidth REAL, aggregate INTEGER, counts BLOB);
      CREATE TABLE sources(seq INTEGER, file TEXT, sha256 TEXT, epoch INTEGER);
      CREATE TABLE excluded(payload TEXT);
      CREATE INDEX segment_bins ON bins(segment,id);
    ''')
    source = readonly(run/'run.sqlite')
    previous = None
    previous_epoch = 0
    segment = -1
    contiguous = False
    next_id = 0
    try:
        head = source.execute('SELECT coalesce(max(seq),-1) FROM snapshots').fetchone()[0]
        for expected, (seq,name,digest,stored_epoch) in enumerate(source.execute(
                'SELECT seq,file,sha256,epoch FROM snapshots WHERE seq<=? ORDER BY seq', (head,))):
            if seq != expected: raise ValueError('Missing/reordered raw snapshot sequence')
            path = (run/name).resolve()
            if not path.is_relative_to(run): raise ValueError('Raw path escapes source folder')
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=digest: raise ValueError('Raw SHA-256 mismatch: '+name)
            current = json.loads(raw)
            if current['seq']!=seq: raise ValueError('Raw sequence differs from index')
            derived = reconstruct(previous,current,config['interval_s'],previous_epoch)
            if derived['epoch']!=stored_epoch: raise ValueError('Raw reconstruction epoch differs from index')
            db.execute('INSERT INTO sources VALUES(?,?,?,?)', (seq,name,digest,stored_epoch))
            begin = midpoint(previous) if previous else None
            finish = midpoint(current)
            selected = epoch is None or epoch==stored_epoch
            inside = selected and begin is not None and (start is None or begin>=start) and (end is None or finish<=end)
            overlap = selected and (start is None or finish>start) and (end is None or begin is None or begin<end)
            flags = set(derived['quality'])
            aggregate = bool(flags & AGGREGATE)
            safe = (derived['counts_delta'] is not None and derived['device_duration_s']>0 and
                    finish>begin and flags <= (ALLOWED | AGGREGATE if keep_aggregates else ALLOWED))
            if inside and safe:
                calibration = json.dumps(current['spectrum']['calibration_keV'])
                if not contiguous or aggregate:
                    segment += 1
                values = np.asarray(derived['counts_delta'],dtype='<i8')
                clock = current.get('clock') or {}
                uncertainty = clock.get('estimated_uncertainty_s')
                db.execute('INSERT INTO bins VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (next_id,segment,seq,stored_epoch,begin,finish,derived['device_duration_s'],int(values.sum()),
                     calibration,json.dumps(derived['quality']),clock.get('quality','not_recorded'),
                     uncertainty,abs(finish-timestamp(current['utc_before'])),int(aggregate),values.tobytes()))
                next_id += 1
                contiguous = not aggregate
            else:
                contiguous = False
                if overlap:
                    db.execute('INSERT INTO excluded VALUES(?)', (json.dumps(dict(seq=seq,epoch=stored_epoch,
                        start_utc=iso(begin) if begin is not None else None,end_utc=iso(finish),
                        device_exposure_s=derived['device_duration_s'],
                        reason='incomplete_range_edge' if not inside and derived['counts_delta'] is not None else ','.join(derived['quality']),
                        counts_delta=derived['counts_delta'])),))
            previous,previous_epoch = current,derived['epoch']
            if seq%2000==0: print(f'Verified source spectrum {seq} / {head}',flush=True)
        db.commit()
    finally:
        source.close()
    return config,head


def ranges(length,mode,width,step,number):
    if mode=='native':
        yield from ((i,i+1) for i in range(length))
    elif mode=='number':
        yield from ((i*length//number,(i+1)*length//number) for i in range(number))
    elif mode=='width':
        yield from ((i,min(length,i+width)) for i in range(0,length,width))
    else:
        yield from ((i,i+width) for i in range(0,length-width+1,step))


def export_stream(run,target=None,mode='native',width_s=None,step_s=None,number=1,start=None,end=None,
                  epoch=None,time_zero=None,xlsx=False,energy_min=None,energy_max=None,
                  keep_aggregates=False,time_zero_basis='auto',chunk_size=256):
    run = Path(run).resolve()
    config = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    if start is not None and end is not None and start>=end: raise ValueError('UTC start must precede end')
    if mode not in ('native','width','number','sliding') or type(number) is not int or number<1:
        raise ValueError('Invalid export mode or number of spectra')
    if time_zero_basis not in ('auto','seal','acquisition','custom'): raise ValueError('Invalid time zero basis')
    if time_zero is not None and not math.isfinite(time_zero): raise ValueError('Invalid time zero')
    if time_zero_basis=='custom' and time_zero is None: raise ValueError('Custom time zero requires a UTC time')
    if any(v is not None and not math.isfinite(v) for v in (energy_min,energy_max,start,end)):
        raise ValueError('Energy/time bounds must be finite')
    if energy_min is not None and energy_max is not None and energy_min>=energy_max:
        raise ValueError('Energy start must precede end')
    if type(chunk_size) is not int or chunk_size<1 or chunk_size>4096: raise ValueError('Invalid chunk size')
    cadence = config['interval_s']
    width = aligned_count(width_s,cadence,'Bin/window width') if mode in ('width','sliding') else 1
    step = aligned_count(step_s,cadence,'Sliding step') if mode=='sliding' else width
    if mode=='sliding' and step>width: raise ValueError('Sliding step cannot exceed averaging width')
    if keep_aggregates and mode=='sliding': raise ValueError('Recovered aggregates require native or nonoverlapping bins')
    if xlsx: import openpyxl
    # Freeze annotation evidence once, before a potentially long raw verification.
    notes = annotations.load(run)
    selected_seal = annotations.seal(notes)
    target = Path(target).resolve() if target else run/('time_resolved_'+dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
    if target.exists(): raise FileExistsError('Refusing to overwrite '+str(target))
    with tempfile.TemporaryDirectory(prefix='gammakeeper_export_') as temporary:
        db = sqlite3.connect(Path(temporary)/'series.sqlite')
        try:
            db.execute('PRAGMA journal_mode=OFF')
            db.execute('PRAGMA cache_size=-8192')
            config,head = stage_source(run,db,start,end,epoch,keep_aggregates)
            segments = list(db.execute('SELECT segment,min(id),count(*),epoch,calibration,max(aggregate) FROM bins GROUP BY segment ORDER BY segment'))
            if not segments: raise ValueError('No complete intervals with valid timing in the selected range')
            if mode=='number' and (len(segments)!=1 or number>segments[0][2]):
                raise ValueError('Number-of-spectra mode requires one contiguous epoch/segment and enough saved intervals')
            if mode=='sliding' and any(length<width for _,_,length,*_ in segments):
                raise ValueError('Averaging window exceeds the available contiguous segment')
            if time_zero is not None:
                zero,basis = time_zero,'custom'
            elif selected_seal and time_zero_basis in ('auto','seal'):
                zero,basis = selected_seal['utc'],'seal'
            elif time_zero_basis=='seal':
                raise ValueError('No seal time recorded')
            else:
                zero,basis = db.execute('SELECT begin FROM bins ORDER BY id LIMIT 1').fetchone()[0],'first_included_interval'
            target.mkdir(parents=True,exist_ok=False)
            annotation_record = dict(notes=notes,last_note_id=max((n['id'] for n in notes),default=0),
                                     selected_seal_id=selected_seal['id'] if selected_seal else None,
                                     snapshot_utc=annotations.iso(__import__('time').time()),
                                     independent_of_acquisition_snapshot=True)
            annotation_record['notes_sha256']=hashlib.sha256(json.dumps(notes,sort_keys=True).encode()).hexdigest()
            immutable_json(target/'ANNOTATIONS.json',annotation_record)
            annotations.export_csv(run,target/'annotations.csv',notes)
            overlapping = mode=='sliding' and step<width
            report = dict(schema='radialog-time-resolved-v1',format_revision=2,software_version=VERSION,analysis_build=identity(),
                source_run=str(run),source_manifest=config,source=dict(source_head=head,
                    manifest_sha256=hashlib.sha256((run/'manifest.json').read_bytes()).hexdigest(),
                    snapshots_file='source_snapshots.jsonl'),mode=mode,requested_width_s=width_s,
                requested_step_s=step_s,requested_number=number if mode=='number' else None,
                start_utc=iso(start) if start is not None else None,end_utc=iso(end) if end is not None else None,
                selected_epoch=epoch,time_zero_utc=iso(zero),time_zero_basis=basis,
                time_zero_note_id=selected_seal['id'] if basis=='seal' else None,
                annotation_snapshot=annotation_record,source_cadence_s=cadence,overlapping=overlapping,
                keep_aggregates=keep_aggregates,excluded_intervals=db.execute('SELECT count(*) FROM excluded').fetchone()[0],
                segments=[],chunk_size=chunk_size,
                processing='Integer sums of complete intervals; no smoothing, interpolation, resampling or synthetic finer bins.',
                timing='UTC read-midpoint boundaries; device-reported exposure retained separately. No calibrated live-time claim.',
                uncertainty='sqrt(counts)/device exposure: counting-only Poisson estimate; not a confidence interval.',
                fitting_notes=['Use bin-integrated model predictions; aggregates retain variable-width boundaries.',
                               'Dead-time/sample-time mapping is uncalibrated; device exposure is separately retained.',
                               'Model progeny ingrowth or justify an exclusion; background and gain require independent QC.'],
                warnings=['Overlapping windows share photons and have correlated errors.'] if overlapping else [])
            if basis=='first_included_interval' and time_zero_basis=='auto':
                report['warnings'].append('No seal annotation; time zero is the first included interval, not experimental sealing.')
            with (target/'source_snapshots.jsonl').open('x',encoding='utf-8') as handle:
                for seq,name,digest,ep in db.execute('SELECT * FROM sources ORDER BY seq'):
                    handle.write(json.dumps(dict(seq=seq,file=name,sha256=digest,epoch=ep))+'\n')
            with (target/'excluded_intervals.jsonl').open('x',encoding='utf-8') as handle:
                for payload, in db.execute('SELECT payload FROM excluded'): handle.write(payload+'\n')
            for index,(segment,first,length,ep,calibration,aggregate_segment) in enumerate(segments):
                calibration = json.loads(calibration)
                nchan = len(db.execute('SELECT counts FROM bins WHERE id=?',(first,)).fetchone()[0])//8
                channels = np.arange(nchan)
                a0,a1,a2 = calibration
                energy = a0+a1*channels+a2*channels**2
                if np.any(np.diff(energy)<=0): raise ValueError('Calibration is not monotonically increasing; no axis reorder was applied')
                select = (energy>=energy_min if energy_min is not None else np.ones(nchan,dtype=bool))
                if energy_max is not None: select &= energy<=energy_max
                if not select.any(): raise ValueError('Energy selection includes no channels')
                group_rows,group_counts = [],[]
                part = 0
                summed = np.zeros(nchan,dtype=np.int64)
                old_a = old_b = first
                def change(a,b,sign):
                    for raw, in db.execute('SELECT counts FROM bins WHERE id>=? AND id<? ORDER BY id',(a,b)):
                        summed[:] += sign*np.frombuffer(raw,dtype='<i8')
                def flush():
                    nonlocal part
                    if not group_rows: return
                    stem=f'segment_{index:03d}_epoch_{ep}'+(f'_part_{part:03d}' if part else '')
                    counts=np.stack(group_counts)
                    exposure=np.array([r['device_exposure_s'] for r in group_rows])
                    rates=counts/exposure[:,None]
                    sigma=np.sqrt(counts)/exposure[:,None]
                    np.savez_compressed(target/(stem+'.npz'),counts=counts,rate_cps=rates,poisson_sigma_cps=sigma,
                        energy_keV=energy,channel=channels,calibration_keV=np.array(calibration),
                        time_s=np.array([r['time_s'] for r in group_rows]),bin_start_s=np.array([r['bin_start_s'] for r in group_rows]),
                        bin_end_s=np.array([r['bin_end_s'] for r in group_rows]),device_exposure_s=exposure,
                        aggregate=np.array([r['aggregate'] for r in group_rows]),
                        first_seq=np.array([r['first_seq'] for r in group_rows]),last_seq=np.array([r['last_seq'] for r in group_rows]))
                    with (target/(stem+'_bins.jsonl')).open('x',encoding='utf-8') as handle:
                        for row in group_rows: handle.write(json.dumps(row)+'\n')
                    header=['Time (s)']+[f'{v*1000:.12g} eV' for v in energy[select]]
                    with (target/(stem+'_rate_cps.csv')).open('x',newline='',encoding='utf-8') as handle:
                        writer=csv.writer(handle);writer.writerow(header)
                        for row,rate in zip(group_rows,rates[:,select]): writer.writerow([row['time_s'],*rate])
                    if xlsx:
                        from openpyxl import Workbook
                        book=Workbook(write_only=True);sheet=book.create_sheet('Spectral rate cps');sheet.append(header)
                        for row,rate in zip(group_rows,rates[:,select]): sheet.append([row['time_s'],*map(float,rate)])
                        book.save(target/(stem+'_UFit_rate_cps.xlsx'))
                    rcspg=None
                    if not overlapping:
                        rcspg=stem+'.rcspg'
                        with (target/rcspg).open('x',encoding='utf-8') as handle:
                            json.dump(rcspg_payload(group_rows,counts,calibration,config,config['label'] or 'GammaKeeper series'),handle)
                        if not np.array_equal(read_rcspg(target/rcspg)[1],counts): raise ValueError('RCSPG count roundtrip failed')
                    lo,hi=group_rows[0]['_first_id'],group_rows[-1]['_last_id']
                    unique,used=db.execute('SELECT coalesce(sum(total),0),count(*) FROM bins WHERE id>=? AND id<=?',(lo,hi)).fetchone()
                    report['segments'].append(dict(stem=stem,epoch=ep,source_segment=index,part=part,bins=len(group_rows),
                        source_intervals=length,intervals_used=used,
                        unused_sliding_tail_intervals=length-(((length-width)//step)*step+width) if mode=='sliding' else 0,
                        calibration_keV=calibration,rcspg=rcspg,matrix_channel_selection=channels[select].tolist(),
                        output_sum_counts=int(counts.sum()),unique_source_counts=unique,
                        start_utc=group_rows[0]['start_utc'],end_utc=group_rows[-1]['end_utc']))
                    print(f'Exported {stem}: {len(group_rows)} spectra',flush=True)
                    part+=1;group_rows.clear();group_counts.clear()
                for a,b in ranges(length,mode,width,step,number):
                    a,b=first+a,first+b
                    if a>=old_b:
                        summed.fill(0);change(a,b,1)
                    else:
                        change(old_a,a,-1);change(old_b,b,1)
                    old_a,old_b=a,b
                    begin,finish,exposure,halfwidth,uncertainty,known_uncertainties,clock_quality,ag=db.execute(
                        'SELECT min(begin),max(end),sum(exposure),max(halfwidth),max(uncertainty),count(uncertainty),group_concat(DISTINCT clock_quality),max(aggregate) FROM bins WHERE id>=? AND id<?',(a,b)).fetchone()
                    seq0,seq1=db.execute('SELECT min(seq),max(seq) FROM bins WHERE id>=? AND id<?',(a,b)).fetchone()
                    flags=sorted(set(flag for text, in db.execute('SELECT DISTINCT flags FROM bins WHERE id>=? AND id<?',(a,b)) for flag in json.loads(text)))
                    row=dict(first_seq=seq0,last_seq=seq1,epoch=ep,source_intervals=b-a,
                        start_epoch_s=begin,end_epoch_s=finish,start_utc=iso(begin),end_utc=iso(finish),
                        time_s=(begin+finish)/2-zero,bin_start_s=begin-zero,bin_end_s=finish-zero,
                        wall_duration_s=finish-begin,device_exposure_s=exposure,total_counts=int(summed.sum()),
                        quality=flags,aggregate=bool(ag),final_short_group=mode=='width' and b-a<width,
                        clock_quality=clock_quality.split(','),clock_estimated_uncertainty_max_s=uncertainty if known_uncertainties==b-a else None,
                        clock_known_uncertainty_max_s=uncertainty,clock_uncertainty_missing_intervals=b-a-known_uncertainties,
                        read_halfwidth_max_s=halfwidth,_first_id=a,_last_id=b-1)
                    group_rows.append(row);group_counts.append(summed.copy())
                    if len(group_rows)>=chunk_size: flush()
                flush()
            report['files']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file()}
            immutable_json(target/'COMPLETE.json',report)
            print(f'Complete: {target}',flush=True)
            return target,report
        finally:
            db.close()
