"""Run summary tab: non-raw acquisition summary, validation verdict and free space."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from PySide6 import QtWidgets as Q
from export_run import readonly


class SummaryMixin:
    def _build_summary_tab(self):
        self.summary_view=Q.QPlainTextEdit();self.summary_view.setReadOnly(True)
        self.tabs.addTab(self.summary_view,'Run summary')

    def refresh_analysis_status(self,state,active,coverage):
        self.refresh_spectrum()
        self.poll_background_jobs()
        summary=state.get('acquisition_summary') or (self.preview_summary if self.view_mode.currentData()=='full' else {})
        if not summary and state.get('last_snapshot') and (state.get('last_interval') or {}).get('epoch',0)==0:
            db=readonly(self.run/'run.sqlite')
            try:
                first=db.execute('SELECT file,sha256 FROM snapshots ORDER BY seq LIMIT 1').fetchone()
            finally:db.close()
            if first:
                payload=(self.run/first[0]).read_bytes()
                if hashlib.sha256(payload).hexdigest()!=first[1]:raise ValueError('Initial spectrum integrity mismatch')
                initial=json.loads(payload);latest=state['last_snapshot']
                start=initial['spectrum'];end=latest['spectrum'];duration=end['duration_s']-start['duration_s']
                if len(start['counts'])==len(end['counts']) and start['calibration_keV']==end['calibration_keV'] and duration>0:
                    counts=sum(end['counts'])-sum(start['counts'])
                    summary=dict(total_counts=counts,device_elapsed_s=duration,mean_spectral_rate_cps=counts/duration,
                                 quality_counts={'source':'endpoint difference; full flags in whole-run view/audit'})
        rows=['RUN SUMMARY',self.run_summary.text(),'']
        import annotations
        current=annotations.seal(getattr(self,'notes',[]))
        if current:
            since=self.current_timeline_seconds()-current['utc']
            rows += [f"Seal time (time zero): {annotations.iso(current['utc'])} UTC • {since/86400:+.4f} d since seal",
                     'Notes: '+str(len(self.notes))+' in annotations.sqlite (Notes tab)','']
        if summary:
            counts=summary.get('total_counts',0);duration=summary.get('device_elapsed_s',0)
            rate=summary.get('mean_spectral_rate_cps')
            rows += [f'Measured interval counts: {counts:,}',f'Summed device duration: {duration:.3f} s',
                     f'Mean spectral rate: {rate:.6f} cps' if rate is not None else 'Mean spectral rate: waiting',
                     'Counting-only Poisson sigma: '+(f'{np.sqrt(counts)/duration:.6f} cps' if duration else 'unavailable'),
                     'Quality flags: '+json.dumps(summary.get('quality_counts',{})), '']
            if not active and state['state']=='completed' and rate is not None:
                self.metrics[0].setText(f'{rate:.2f} cps');self.metric_captions[0].setText('Spectrum rate • whole run')
        rows += [f"Saved spectra: {state.get('snapshots',0):,}",f'Recomputed dose time coverage: {coverage:.5%}' if coverage is not None else 'Loading saved telemetry; final coverage not yet available.',
                 'Dose time basis: received estimator observations; ≤3 s zero-order hold; sample/filter latency uncalibrated.',
                 'Spectral rate uses actual device duration; one-second device duration quantization is retained.',
                 'Terminal channel is retained in totals; its overflow semantics require confirmation before peak interpretation.','']
        snapshot=state.get('last_snapshot') or {}
        counter=snapshot.get('accumulated_dose') or {}
        if counter:
            from analysis import midpoint,timestamp
            age=midpoint(snapshot)-timestamp(counter['dt'])
            rows += [f"Native accumulated-dose difference: {snapshot.get('device_experiment_dose_uSv',0):.5f} µSv",
                     f'Native counter record age at spectrum endpoint: {age:.1f} s',
                     'This older counter is separate from the received-estimator integral.','']
        target=(state.get('supervisor') or {}).get('completion_target')
        if not target:
            candidates=sorted(self.run.glob('completion_*/VALIDATION.json'))
            target=str(candidates[-1].parent) if candidates else None
        if target:
            file=Path(target)/'VALIDATION.json'
            if file.exists():
                verdict=json.loads(file.read_text(encoding='utf-8'))
                rows += ['INTEGRITY VALIDATION: '+('PASSED' if verdict.get('passed') else 'FAILED'),
                         'Data quality: '+verdict.get('data_quality','review report'),
                         'Report: '+str(file)]
                if not verdict.get('passed'):rows.append(verdict.get('error',''))
            else:rows += ['Completion audit/export: running in background; capture status is separate.']
        else:rows += ['Completion audit/export: not yet run.']
        if time.monotonic()-self._last_disk>10:
            import shutil
            self._last_disk=time.monotonic();self._disk_free=shutil.disk_usage(self.run).free
        free=getattr(self,'_disk_free',0)
        rows += ['',f'Free space on recording volume: {free/1024**3:.2f} GiB',
                 'Recording stops below 256 MiB free; reserve space for exports and backups.']
        self.summary_view.setPlainText('\n'.join(rows))
        self.audit_button.setEnabled(state['state']=='completed' and not any(j['script']=='completion.py' for j in self.background_jobs))
        self.refresh_events()
