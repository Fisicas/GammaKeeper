"""Time-series canvas: spectrogram, count rate and dose rate."""
import datetime as dt

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
from plot_scales import apply_scale,display_values,intensity_norm
from ui.theme import BG, PANEL, TEXT, MUTED, TEAL, GOLD


def time_format(span_days, show_dates=False):
    """Tick label format for a time span (days): seconds for short windows, dates for long ones."""
    if span_days > 1.5 or (show_dates and span_days > .5):
        return '%m-%d %H:%M'
    return '%H:%M:%S' if span_days <= 15 / 1440 else '%H:%M'


class Charts(FigureCanvasQTAgg):
    def __init__(self):
        self.figure = Figure(figsize=(10, 7), facecolor=BG, layout='constrained')
        super().__init__(self.figure)
        grid = self.figure.add_gridspec(2, 2, height_ratios=[2.3, 1])
        self.spg = self.figure.add_subplot(grid[0, :])
        self.count = self.figure.add_subplot(grid[1, 0])
        self.dose = self.figure.add_subplot(grid[1, 1])
        self.colorbar = None
        self.display_label='Recent recorded intervals'
        self.color_percentile=99.5
        self.local_time=False
        self.show_dates=False
        self.colormap='viridis'
        self.roi=None
        self.energy_scale='linear';self.counts_scale='linear';self.intensity_scale='log'
        self.scale_note=''
        self.empty()

    def style(self, ax):
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=MUTED, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#314357')
        ax.xaxis.label.set_color(MUTED)
        ax.yaxis.label.set_color(MUTED)
        ax.title.set_color(TEXT)
        ax.title.set_fontsize(10)

    def empty(self):
        for ax, title in [(self.spg, 'Energy spectrum over time'), (self.count, 'Count rate'), (self.dose, 'Dose rate')]:
            self.style(ax)
            ax.set_title(title, loc='left')
            ax.text(.5, .5, 'Waiting for recorded data', transform=ax.transAxes, ha='center', va='center', color=MUTED)
        apply_scale(self.spg,'x',self.energy_scale)
        apply_scale(self.count,'y',self.counts_scale)
        self.draw_idle()

    def show_rows(self, rows, last_snapshot, mode):
        if self.colorbar:
            self.colorbar.remove()
            self.colorbar = None
        for ax in [self.spg, self.count, self.dose]:
            ax.clear()
            self.style(ax)
        spectrum = last_snapshot['spectrum']
        channels = len(spectrum['counts'])
        edge_ch = np.arange(channels + 1) - .5
        a0, a1, a2 = spectrum['calibration_keV']
        energy = a0 + a1 * edge_ch + a2 * edge_ch**2
        ends, starts, matrix, rates, dose_rates, filtered, roi_rates = [], [], [], [], [], [], []
        for utc, epoch, d, s in rows:
            if d['device_duration_s'] is None or d['wall_duration_s'] is None or d['wall_duration_s'] <= 0:
                continue
            end = mdates.date2num(dt.datetime.fromtimestamp(utc, dt.timezone.utc))
            start = end - d['wall_duration_s'] / 86400
            is_normal = d['quality'] == ['normal'] and not s.get('final_partial_interval')
            delta = d['counts_delta']
            if delta is not None and len(delta) == channels and s['spectrum']['calibration_keV'] == spectrum['calibration_keV']:
                values = np.asarray(delta, dtype=float) / d['device_duration_s'] if is_normal else np.full(channels, np.nan)
            else:
                values = np.full(channels, np.nan)
            starts.append(start)
            ends.append(end)
            matrix.append(values)
            rates.append(d['count_rate_cps'] if is_normal else np.nan)
            if self.roi and delta is not None and is_normal:
                centers=a0+a1*np.arange(channels)+a2*np.arange(channels)**2
                mask=(centers>=self.roi[0])&(centers<=self.roi[1]);mask[-1]=mask[-1] and self.roi[2]
                roi_rates.append(float(np.asarray(delta)[mask].sum())/d['device_duration_s'])
            else:roi_rates.append(np.nan)
            tb = s.get('telemetry_bin', {})
            coverage = tb.get('coverage_fraction', 0)
            dose_rates.append(tb.get('dose_rate_uSv_h') if coverage >= .999 and is_normal else np.nan)
            filtered.append(tb.get('device_count_rate_cps') if coverage >= .999 and is_normal else np.nan)
        span = (ends[-1] - starts[0]) if ends else 0.
        prefix = 'SIMULATED • ' if mode == 'demo' else ''
        self.spg.set_title(prefix + 'Spectrogram • '+self.display_label+' • gaps left blank', loc='left')
        if ends and np.all(np.diff(energy) > 0):
            yedges = [starts[0]] + ends
            values = np.ma.masked_invalid(np.asarray(matrix))
            values,norm=intensity_norm(values,self.intensity_scale,self.color_percentile)
            if self.energy_scale=='log':
                values=np.ma.masked_where(np.broadcast_to(energy[:-1]<=0,values.shape),values)
            cmap = __import__('matplotlib').colormaps[self.colormap].with_extremes(bad=PANEL)
            mesh = self.spg.pcolormesh(energy, yedges, values,
                                     cmap=cmap, norm=norm, shading='flat', rasterized=True)
            self.colorbar = self.figure.colorbar(mesh, ax=self.spg, pad=.02, fraction=.025)
            self.colorbar.set_label('counts/s/channel • '+self.intensity_scale, color=MUTED, fontsize=8)
            # which='both': log color scales label minor ticks (2x10^-1, 3x10^-1, ...) too.
            self.colorbar.ax.tick_params(which='both', colors=MUTED, labelsize=8)
            self.spg.invert_yaxis()
            self.spg.yaxis.set_major_locator(mdates.AutoDateLocator(tz=dt.timezone.utc, minticks=3, maxticks=8))
            self.spg.yaxis.set_major_formatter(mdates.DateFormatter(time_format(span, self.show_dates), tz=dt.timezone.utc))
            self.count.plot(ends, display_values(rates,self.counts_scale=='log'), color=TEAL, linewidth=1.4,
                            marker='.' if self.counts_scale=='log' else None,markersize=3,label='Spectrum differences')
            self.count.plot(ends, display_values(filtered,self.counts_scale=='log'), color=MUTED, linewidth=1., linestyle='--', label='Device estimator')
            if self.roi:self.count.plot(ends,display_values(roi_rates,self.counts_scale=='log'),color=GOLD,linewidth=1.,label='Selected ROI')
            self.dose.plot(ends, dose_rates, color=GOLD, linewidth=1.4)
            self.count.legend(facecolor=PANEL, edgecolor=PANEL, labelcolor=MUTED, fontsize=7, loc='upper left')
        else:
            self.spg.text(.5, .5, 'Baseline saved; waiting for a complete interval', transform=self.spg.transAxes, ha='center', color=MUTED)
        self.spg.set_xlabel('Energy (keV) • device polynomial; original channels')
        self.spg.set_ylabel('UTC • time increases downward')
        if self.local_time:
            self.spg.set_ylabel('Local time • UTC offset shown • time increases downward')
            local_format = time_format(span, self.show_dates) + ' %z'
            self.spg.yaxis.set_major_formatter(FuncFormatter(lambda value,pos:mdates.num2date(value,dt.timezone.utc).astimezone().strftime(local_format)))
        self.count.set_title('Counts over time', loc='left')
        self.count.set_ylabel('cps')
        self.dose.set_title('Device dose-rate mean over time', loc='left')
        self.dose.set_ylabel('µSv/h')
        for ax in [self.count, self.dose]:
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=dt.timezone.utc, minticks=3, maxticks=7))
            ax.xaxis.set_major_formatter(mdates.DateFormatter(time_format(span, self.show_dates), tz=dt.timezone.utc))
            ax.set_xlabel('UTC')
            if self.local_time:
                ax.xaxis.set_major_formatter(FuncFormatter(lambda value,pos:mdates.num2date(value,dt.timezone.utc).astimezone().strftime(local_format)))
                ax.set_xlabel('Local time • original timestamps are UTC')
            ax.grid(alpha=.12, color=MUTED)
            if len(ends)==1:ax.set_xlim(starts[0],ends[0])
        apply_scale(self.spg,'x',self.energy_scale,energy)
        apply_scale(self.count,'y',self.counts_scale,[*rates,*filtered,*roi_rates])
        if self.counts_scale=='log' and ends and not display_values([*rates,*filtered,*roi_rates],True).count():
            self.count.text(.5,.5,'No positive count rates\nChoose Linear to display zeros',transform=self.count.transAxes,ha='center',color=MUTED)
        notes=[]
        if self.energy_scale=='log':notes.append('Log energy shows only bins with positive energy edges')
        if self.counts_scale=='log':notes.append('Log count-rate axis hides zero/nonpositive values')
        if self.intensity_scale=='log':notes.append('Log intensity hides zero bins; Linear shows measured zeros')
        self.scale_note=' • '.join(notes)
        self.draw_idle()
