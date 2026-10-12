"""Periodic status refresh: state banner, metrics, health, clock footer."""
import datetime as dt
import hashlib
import sqlite3

from analysis import midpoint, timestamp, DOSE_R_TO_USV
from export_run import readonly
from saved_status import load_status
from telemetry_analysis import LiveJournalTelemetry
from run_policy import resume_allowed
from system_time import boot_identity
from ui.paths import ROOT
from ui.theme import TEAL, GOLD, MUTED


class RunStatusMixin:
    def refresh(self):
        self.poll_background_jobs()
        if self.export_process and self.export_process.poll() is not None:
            code = self.export_process.returncode
            self.export_log.close()
            self.export_process = None
            self.export_button.setEnabled(True)
            self.message(f'Export {"complete" if code == 0 else "failed; see export log"}: {self.export_destination}')
        if not self.run:
            return
        if not self.run.exists():
            if self.recorder and self.recorder.poll() is not None:
                self.connection.setText('START FAILED — see diagnostics log')
                self.start_button.setEnabled(True)
                self.stop_button.setEnabled(False)
                self.detail.setText(str(self.last_launch_log))
            return
        try:
            self.restore_run_settings()
            self.setup_button.setEnabled(not self.active())
            state = load_status(self.run)
            if not state:
                if self.recorder and self.recorder.poll() is not None:
                    self.connection.setText('START FAILED — see diagnostics log')
                    self.start_button.setEnabled(False)
                    self.resume_button.setEnabled(True)
                return
            age = dt.datetime.now(dt.timezone.utc).timestamp() - timestamp(state['utc'])
            self.status_stale = age > 25 and state['state'] in ('starting', 'connecting', 'recording', 'reconnecting')
            self.last_status = state
            active = self.active()
            self.metric_captions[0].setText('Spectrum rate • latest interval' if active else 'Spectrum rate • final interval')
            self.metric_captions[1].setText('Dose rate • latest estimator' if active else 'Dose rate • final estimator')
            prefix = 'SIMULATED • ' if state['mode'] == 'demo' else ''
            name = 'STATUS STALE — recorder may have stopped' if self.status_stale else state['state'].upper()
            sup = state.get('supervisor') or {}
            allowed,reason=resume_allowed(self.run)
            booted = state.get('system_boot_id')
            rebooted = bool(booted) and booted != boot_identity()
            if sup.get('status') == 'restart_wait' and active:
                name = 'RESTARTING RECORDER'
            elif sup.get('status') == 'attention_required':
                name = 'ATTENTION REQUIRED — recording stopped'
            elif state['state'] == 'interrupted_by_shutdown' or (state['state'] in ('recording','reconnecting','connecting') and not active and rebooted):
                cause = 'WINDOWS SHUTDOWN' if state['state'] == 'interrupted_by_shutdown' else 'WINDOWS RESTART (no warning received)'
                name = f'INTERRUPTED BY {cause} — ' + ('resume available' if allowed else 'schedule ended')
            elif state['state'] in ('recording','reconnecting','connecting') and not active:
                name = 'RECORDER EXITED — resume available'
            if (self.run/'BACKUP_PLAN.json').exists():name='BACKUP SNAPSHOT'
            recovery=sup.get('recovery_progress')
            if recovery and active:name=f"RECOVERING SAVED DATA • {recovery['verified']:,} verified"
            self.connection.setText(prefix + name)
            warning_color = '#ff7b86' if ('ERROR' in name or 'ATTENTION' in name or 'EXITED' in name or 'STALE' in name) else GOLD if ('RECONNECT' in name or 'RESTART' in name or 'INTERRUPTED' in name) else TEAL
            self.connection.setStyleSheet(f'color: {warning_color}; font-weight: 600;')
            self.start_button.setEnabled(False)
            self.stop_button.setEnabled(active)
            self.resume_button.setEnabled(not active and allowed)
            self.resume_button.setToolTip(reason or 'Continue the original saved schedule')
            self.setup_button.setEnabled(not active)
            self.metrics[3].setText(f"{state.get('snapshots', 0):,}")
            db=readonly(self.run/'run.sqlite')
            try:
                first=db.execute('SELECT utc FROM snapshots ORDER BY seq LIMIT 1').fetchone()
                endpoint=midpoint(state['last_snapshot']) if state.get('last_snapshot') and state['state'] in ('completed','stopped') else None
                analysis_key=str(self.run.resolve())+'|'+str(endpoint)
                if first and self.telemetry_run!=analysis_key:
                    self.telemetry_reader=LiveJournalTelemetry(first[0],state.get('interval_s',10)*240,end_utc=endpoint)
                    cache_key=hashlib.sha256(((self.run/'manifest.json').read_text()+analysis_key).encode()).hexdigest()
                    self.telemetry_reader.restore_checkpoint(db,ROOT/'cache'/'telemetry'/f'{cache_key}.sqlite')
                    self.telemetry_run=analysis_key
                caught_up=self.telemetry_reader.update(db) if self.telemetry_reader else False
                if self.telemetry_reader:self.telemetry_reader.checkpoint(db)
                observation=self.telemetry_reader.previous if self.telemetry_reader else None
                coverage=self.telemetry_reader.coverage() if self.telemetry_reader else 0.
            finally:
                db.close()
            snapshot = state.get('last_snapshot')
            interval = state.get('last_interval', {})
            if snapshot:
                rate = interval.get('count_rate_cps')
                self.metrics[0].setText(f'{rate:.2f} cps' if rate is not None else 'Baseline')
                rt = observation or {}
                dose = rt.get('dose_rate')
                self.metrics[1].setText(f'{dose * DOSE_R_TO_USV:.3f} µSv/h' if dose is not None else '—')
                rare = snapshot.get('accumulated_dose') or {}
                experiment_dose = self.telemetry_reader.dose_uSv if self.telemetry_reader and caught_up else None
                self.metrics[2].setText(f'{experiment_dose:.4f} µSv' if experiment_dose is not None else 'Waiting')
                self.metrics[2].setToolTip(f'Recomputed from received real-time estimators. Time coverage {coverage:.2%}; host reception UTC. Exact device sample/filter latency is unverified. Original device timestamps remain saved.')
                seq = snapshot['seq']
                render_key=(str(self.run),seq,caught_up)
                if self.last_seq != render_key:
                    self.request_preview()
                    if not self.last_seq or self.last_seq[1]!=seq:
                        self.message(f'Saved spectrum {seq}; ' + ', '.join(interval.get('quality', [])))
                    self.last_seq=render_key
            retry = f" • retry in {state.get('retry_delay_s')} s" if state['state'] == 'reconnecting' else ''
            saved_snapshot = state.get('last_snapshot') or {}
            coverage_text = f' • dose time coverage: {coverage:.4%}' if caught_up else ' • loading dose coverage estimate'
            now=self.current_timeline_seconds()
            spectrum_age=now-midpoint(snapshot) if snapshot else None
            telemetry_age=now-timestamp(observation['dt']) if observation else None
            lead=observation.get('device_projection_lead_s') if observation else None
            age_text=f'Spectrum age: {spectrum_age:.1f} s' if spectrum_age is not None else 'No spectrum yet'
            telemetry_text=f'telemetry age: {telemetry_age:.1f} s' if telemetry_age is not None else 'No telemetry yet'
            lead_text=f'device clock projection: {lead:+.2f} s vs reception' if lead is not None else 'device time unverified'
            progress=' • loading saved telemetry' if not caught_up else ''
            self.health.setText(f'{age_text} • {telemetry_text} • supervisor restarts: {sup.get("restarts",0)} • {lead_text}{progress}\nDose uses host-reception observations; exact sample/filter latency is not calibrated.')
            self.health.setStyleSheet(f'color: {GOLD if active and ((spectrum_age and spectrum_age>max(30,state.get("interval_s",10)*3)) or (telemetry_age and telemetry_age>5)) else MUTED};')
            if not active and state['state'] in ('completed','stopped'):
                self.health.setText(f"Saved final spectrum: {(snapshot or {}).get('utc_after','unknown')} • supervisor restarts: {sup.get('restarts',0)} • {lead_text}\nCompleted data are static; dose uses received estimators with uncalibrated sample/filter latency.")
            self.refresh_analysis_status(state,active,coverage if caught_up else None)
            self.refresh_experiment(state,active)
            if getattr(self,'read_only',False):
                for widget in (self.start_button,self.stop_button,self.resume_button,self.setup_button,
                               self.seal_button,self.note_button,self.sample_button,self.monitor_button):widget.setEnabled(False)
            diagnostics = ''
            if state.get('status_cache_warning'):
                diagnostics += '\nDisplay cache unavailable; reading saved journal. Recording continues.'
            if state.get('decoder_warning'):
                diagnostics += '\nTelemetry decoder warning; raw payload retained. See events for details.'
            if state.get('raw_preview_error'):
                diagnostics += '\nRaw preview unavailable: '+state['raw_preview_error']
            if sup.get('reason'):
                diagnostics += '\n'+sup['reason']
            self.detail.setText(f"{self.run}\n{state.get('message', '')}{retry} • retries: {state.get('retries', 0)}{coverage_text}{diagnostics}")
            cm = (snapshot or {}).get('clock') or state.get('clock_measurement') or {}
            timing = f"SNTP {cm.get('server')} • offset {cm['offset_s']:+.3f} s • estimated ±{cm['estimated_uncertainty_s']:.3f} s" if cm.get('verified') else 'UTC accuracy unverified • host clock fallback'
            quality=cm.get('quality','legacy referenced' if cm.get('verified') else 'unverified')
            reference_age=cm.get('reference_age_s')
            detail=f" • reference age {reference_age:.0f} s" if reference_age is not None else ''
            if cm.get('quality')=='holdover':timing='Monotonic UTC holdover; fresh network reference unavailable'
            self.footer.setText(('Clock at last read' if not active else 'Clock')+f': {quality}{detail} • '+timing+' • sample/filter latency remains uncalibrated')
            self.footer.setToolTip(cm.get('uncertainty_note','SNTP error estimate is not a certified timing guarantee'))
        except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as error:
            if self.run_settings is None:
                self.run_summary.setText(f'Saved run settings unavailable: {error}')
                self.resume_button.setEnabled(False)
                self.setup_button.setEnabled(not self.active())
            self.detail.setText(f'Waiting for saved state: {error}')
