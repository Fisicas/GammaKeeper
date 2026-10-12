"""Spectrum / ROI tab: interval and accumulated spectra."""
import datetime as dt
import json
import sqlite3
import threading

import numpy as np
from PySide6 import QtCore, QtWidgets as Q
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg,NavigationToolbar2QT
from export_run import readonly
from spectrum_view import latest_interval,AccumulatedSpectrum
from plot_scales import apply_scale,display_values
import display_controls
from ui.tasks import AccumulationTask


class SpectrumCanvas(FigureCanvasQTAgg):
    def __init__(self,bg,panel,text):
        self.figure=Figure(figsize=(10,6),facecolor=bg,layout='constrained')
        super().__init__(self.figure)
        self.ax=self.figure.add_subplot(111)
        self.panel,self.text=panel,text
        self.last_counts=None
        self.last_roi_counts=None
        self.energy_scale='linear';self.counts_scale='linear'
        self.scale_note=''
        self.display_row=None
        self.empty('Waiting for a recorded interval. The first spectrum is a baseline.')

    def style(self):
        self.ax.set_facecolor(self.panel)
        self.ax.tick_params(colors=self.text)
        for spine in self.ax.spines.values():spine.set_color(self.text)
        self.ax.xaxis.label.set_color(self.text);self.ax.yaxis.label.set_color(self.text)

    def empty(self,message):
        self.ax.clear();self.style();self.last_counts=None;self.last_roi_counts=None
        self.display_row=None;self.scale_note=''
        self.ax.text(.5,.5,message,ha='center',va='center',color=self.text,transform=self.ax.transAxes,wrap=True)
        self.ax.set_xlabel('Energy (keV) • original channel calibration')
        self.ax.set_ylabel('Recorded interval counts / channel')
        apply_scale(self.ax,'x',self.energy_scale)
        apply_scale(self.ax,'y',self.counts_scale)
        self.draw_idle()

    def show_interval(self,row,roi=None):
        utc,epoch,d,s=row
        self.ax.clear();self.style();self.last_counts=None;self.last_roi_counts=None
        self.display_row=row;self.scale_note=''
        counts=d.get('counts_delta')
        if counts is None:
            self.ax.text(.5,.5,'No independent spectrum for this boundary',ha='center',color=self.text,transform=self.ax.transAxes)
        else:
            self.last_counts=list(counts)
            channels=np.arange(len(counts));a0,a1,a2=s['spectrum']['calibration_keV']
            energy=a0+a1*channels+a2*channels**2
            x=display_values(energy,self.energy_scale=='log')
            y=display_values(counts,self.counts_scale=='log')
            if self.energy_scale=='log':y=np.ma.masked_where(energy<=0,y)
            self.ax.step(x[:-1],y[:-1],where='mid',color='#43d8bf',linewidth=1.,
                         marker='.' if self.counts_scale=='log' else None,markersize=3)
            self.ax.plot(x[-1:],y[-1:],'x',color='#f5bc66',label='Terminal channel • overflow interpretation requires confirmation')
            notes=[]
            if self.energy_scale=='log' and np.any(energy<=0):notes.append('Nonpositive energies hidden on log axis')
            if self.counts_scale=='log' and np.any(np.asarray(counts)<=0):notes.append('Zero/nonpositive counts hidden on log axis; no pseudocount added')
            if np.ma.count(y)==0:notes.append('No positive values to display; choose Linear')
            self.scale_note=' • '.join(notes)
            if self.scale_note:self.ax.text(.02,.03,self.scale_note,transform=self.ax.transAxes,color=self.text,fontsize=8)
            if roi:
                mask=(energy>=roi[0])&(energy<=roi[1]);mask[-1]=mask[-1] and roi[2]
                selected=int(np.asarray(counts)[mask].sum())
                self.last_roi_counts=selected
                lower=roi[0]
                if self.energy_scale=='log':
                    positive=energy[energy>0]
                    lower=max(lower,float(positive.min())) if len(positive) else roi[1]
                if lower<roi[1]:self.ax.axvspan(lower,roi[1],color='#f5bc66',alpha=.15)
                self.ax.text(.02,.96,f'ROI: {selected:,} counts',va='top',transform=self.ax.transAxes,color=self.text)
            self.ax.legend(facecolor=self.panel,labelcolor=self.text,fontsize=8)
        self.ax.set_xlabel('Energy (keV) • original channel calibration',color=self.text)
        self.ax.set_ylabel('Recorded interval counts / channel',color=self.text)
        end=dt.datetime.fromtimestamp(utc,dt.timezone.utc).isoformat(timespec='seconds')
        collection=s.get('collection')
        if collection:
            start=dt.datetime.fromtimestamp(collection['actual_start_utc'],dt.timezone.utc).isoformat(timespec='seconds')
            self.ax.set_ylabel('Accumulated recording counts / channel',color=self.text)
            self.ax.set_title(f'{start} → {end}\n{collection["included_intervals"]:,} recorded intervals • epochs {collection["epochs"]}',color=self.text,fontsize=10)
        else:self.ax.set_title(f'{end} • epoch {epoch} • '+', '.join(d['quality']),color=self.text,fontsize=10)
        apply_scale(self.ax,'x',self.energy_scale,energy if counts is not None else ())
        apply_scale(self.ax,'y',self.counts_scale,counts if counts is not None else ())
        self.draw_idle()


class SpectrumTabMixin:
    def _build_spectrum_tab(self,bg,panel,text):
        """Spectrum / ROI tab: source selector, collection range, mirrored ROI and canvas."""
        spectrum_page=Q.QWidget();spectrum_layout=Q.QVBoxLayout(spectrum_page)
        spectrum_controls=Q.QHBoxLayout()
        self.spectrum_mode=Q.QComboBox();self.spectrum_mode.addItems(['Latest recorded interval','Selected heatmap interval / group','Accumulated recording','Accumulated collection range (UTC)'])
        self.spectrum_mode.setCurrentIndex(2)
        self.spectrum_refresh=Q.QPushButton('Refresh spectrum')
        spectrum_controls.addWidget(Q.QLabel('Spectrum source'));spectrum_controls.addWidget(self.spectrum_mode,1)
        spectrum_controls.addWidget(self.spectrum_refresh)
        spectrum_layout.addLayout(spectrum_controls)
        spectrum_layout.addLayout(display_controls.controls(self,True))
        collection_controls=Q.QHBoxLayout()
        self.collection_from=Q.QDateTimeEdit();self.collection_until=Q.QDateTimeEdit()
        for edit in (self.collection_from,self.collection_until):
            edit.setDisplayFormat('yyyy-MM-dd HH:mm:ss.zzz');edit.setCalendarPopup(True);edit.setTimeZone(QtCore.QTimeZone(0))
        self.collection_latest=Q.QCheckBox('Until latest saved spectrum');self.collection_latest.setChecked(True)
        self.collection_epoch=Q.QComboBox();self.collection_epoch.addItem('All compatible epochs',None)
        self.collection_apply=Q.QPushButton('Apply range')
        self.collection_apply.clicked.connect(lambda:self.refresh_spectrum(True))
        for widget in (Q.QLabel('UTC from'),self.collection_from,Q.QLabel('to'),self.collection_until,self.collection_latest,self.collection_epoch):collection_controls.addWidget(widget)
        collection_controls.addWidget(self.collection_apply)
        spectrum_layout.addLayout(collection_controls)
        self.collection_until.setEnabled(False)
        self.collection_from.setToolTip('Only complete saved intervals within the requested UTC bounds are summed. Click Apply range after editing.')
        self.collection_until.setToolTip(self.collection_from.toolTip())
        self.collection_latest.toggled.connect(lambda checked:self.collection_until.setEnabled(not checked))
        spectrum_roi=Q.QHBoxLayout()
        self.spectrum_roi_enabled=Q.QCheckBox('ROI');self.spectrum_roi_enabled.setChecked(self.roi_enabled.isChecked())
        self.spectrum_roi_from=Q.QDoubleSpinBox();self.spectrum_roi_to=Q.QDoubleSpinBox()
        for control in (self.spectrum_roi_from,self.spectrum_roi_to):control.setRange(0,10000);control.setDecimals(1);control.setSuffix(' keV')
        self.spectrum_roi_from.setValue(self.roi_from.value());self.spectrum_roi_to.setValue(self.roi_to.value())
        self.spectrum_roi_terminal=Q.QCheckBox('Include terminal channel')
        for widget in (self.spectrum_roi_enabled,self.spectrum_roi_from,self.spectrum_roi_to,self.spectrum_roi_terminal):spectrum_roi.addWidget(widget)
        spectrum_roi.addStretch();spectrum_layout.addLayout(spectrum_roi)
        self.spectrum_note=Q.QLabel('Accumulated recording grows as data arrive. Choose a collection range and click Apply range to sum complete intervals in that range.')
        self.spectrum_note.setWordWrap(True);self.spectrum_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        spectrum_layout.addWidget(self.spectrum_note)
        self.spectrum_canvas=SpectrumCanvas(bg,panel,text)
        self.spectrum_display_note=Q.QLabel('Display only: logarithmic axes show positive values; raw counts and ROI sums are unchanged.')
        self.spectrum_display_note.setWordWrap(True)
        spectrum_layout.addWidget(self.spectrum_display_note)
        self.spectrum_toolbar=NavigationToolbar2QT(self.spectrum_canvas,self)
        spectrum_layout.addWidget(self.spectrum_toolbar);spectrum_layout.addWidget(self.spectrum_canvas)
        self.tabs.addTab(spectrum_page,'Spectrum / ROI')

    def spectrum_tab_changed(self,index):
        if index==1:self.refresh_spectrum(True)

    def refresh_spectrum(self,force=False):
        if not hasattr(self,'spectrum_canvas') or self._closing:return
        if self.tabs.currentIndex()!=1 and not force:return
        if not self.run or not (self.run/'run.sqlite').exists():
            self.spectrum_canvas.empty('Open a recording or start one to view its interval spectra.')
            self.spectrum_note.setText('No recording selected.');return
        roi=(self.roi_from.value(),self.roi_to.value(),self.roi_terminal.isChecked()) if self.roi_enabled.isChecked() else None
        if roi and roi[1]<=roi[0]:
            self.spectrum_note.setText('ROI upper energy must exceed lower energy.');return
        try:
            db=readonly(self.run/'run.sqlite')
            try:epochs=[row[0] for row in db.execute('SELECT DISTINCT epoch FROM snapshots ORDER BY epoch')]
            finally:db.close()
            if self.collection_epoch.count()!=len(epochs)+1:
                current=self.collection_epoch.currentData();self.collection_epoch.blockSignals(True)
                self.collection_epoch.clear();self.collection_epoch.addItem('All compatible epochs',None)
                for epoch in epochs:self.collection_epoch.addItem(f'Epoch {epoch}',epoch)
                self.collection_epoch.setCurrentIndex(max(0,self.collection_epoch.findData(current)));self.collection_epoch.blockSignals(False)
            if self.spectrum_mode.currentIndex()==0:
                row=latest_interval(self.run,self.collection_epoch.currentData())
                label='Latest recorded interval'
            elif self.spectrum_mode.currentIndex()==1:
                row=self.selected_analysis_row;label='Selected heatmap interval / group'
            else:
                start=self.collection_from.dateTime().toMSecsSinceEpoch()/1000 if self.spectrum_mode.currentIndex()==3 else None
                end=self.collection_until.dateTime().toMSecsSinceEpoch()/1000 if self.spectrum_mode.currentIndex()==3 and not self.collection_latest.isChecked() else None
                if end is not None and start>=end:raise ValueError('Collection start must precede its end')
                key=(str(self.run.resolve()),self.spectrum_mode.currentIndex(),start,end,self.collection_epoch.currentData())
                if self.accumulation_key!=key:
                    self.accumulation_cancel.set();self.accumulation_key=key;self.accumulation_engine=None
                    self.accumulation_row=None;self.accumulation_head=-1;self.accumulation_error=None
                    self.spectrum_canvas.empty('Accumulating complete recorded intervals…')
                head=(self.last_status.get('last_snapshot') or {}).get('seq',-1)
                if not self.accumulation_busy and (self.accumulation_head<head or force):
                    if self.accumulation_engine is None:
                        self.accumulation_engine=AccumulatedSpectrum(self.run,start,end,self.collection_epoch.currentData())
                    self.accumulation_cancel=threading.Event();self.accumulation_busy=True
                    task=AccumulationTask(self.accumulation_engine,key,self.accumulation_cancel);self._accumulation_task=task
                    task.signals.progress.connect(self.spectrum_note.setText);task.signals.done.connect(self.accumulation_done)
                    self.accumulation_pool.start(task)
                if self.accumulation_error:raise ValueError(self.accumulation_error)
                row=self.accumulation_row
                label='Accumulated recording' if self.spectrum_mode.currentIndex()==2 else 'Accumulated collection range'
                if row is None:
                    self.spectrum_note.setText(label+' • loading complete intervals' if self.accumulation_busy else label+' • no complete recorded intervals inside this range')
                    if not self.accumulation_busy:self.spectrum_canvas.empty('No complete recorded intervals in this collection range. Expand the bounds or choose another epoch.')
                    return
            if row is None:
                self.spectrum_canvas.empty('Waiting for a recorded interval.' if self.spectrum_mode.currentIndex()==0 else 'Select an interval on the time-series heatmap, or choose Latest recorded interval.')
                self.spectrum_note.setText(label+' • no interval available yet');return
            key=(str(self.run.resolve()),self.spectrum_mode.currentIndex(),row[0],repr(row[1]),repr(roi),self.collection_epoch.currentData(),self.energy_axis.currentData(),self.counts_axis.currentData())
            if key==self._spectrum_key and not force:return
            self.spectrum_canvas.show_interval(row,roi);self._spectrum_key=key
            d=row[2];counts=d.get('counts_delta')
            total=sum(counts) if counts is not None else None
            text=f'{label} • epoch {row[1]} • '+', '.join(d['quality'])
            if total is not None:text+=f" • {total:,} counts • device duration {d['device_duration_s']:g} s"
            else:text+=' • baseline only; waiting for the next spectrum difference'
            if roi:
                selected=self.spectrum_canvas.last_roi_counts
                text+=f' • ROI {selected:,} counts' if selected is not None else ''
            collection=row[3].get('collection')
            if collection:
                text+=f" • {collection['included_intervals']:,} complete intervals • {collection['excluded_boundary_intervals']} straddling intervals excluded"
                cautions={key:value for key,value in collection['quality_counts'].items() if key not in ('normal','final_short_interval')}
                if cautions:text+=' • flags '+json.dumps(cautions)
                text+=' • no splitting or resampling'
            self.spectrum_note.setText(text)
        except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
            self.spectrum_canvas.empty('Spectrum unavailable; see the message above.')
            self.spectrum_note.setText(f'Spectrum unavailable: {error}')

    def accumulation_done(self,message):
        self.accumulation_busy=False
        if self._closing:return
        if message['key']!=self.accumulation_key:
            self.refresh_spectrum(True);return
        if message.get('cancelled'):return
        self.accumulation_row=message['row'];self.accumulation_head=message['head'];self.accumulation_error=message['error']
        self.refresh_spectrum(False)
