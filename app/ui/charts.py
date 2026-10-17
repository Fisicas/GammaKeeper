"""Live dashboard canvas: spectrogram, accumulated spectrum and rate trends."""
import datetime as dt

import numpy as np
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
import matplotlib.dates as mdates
from matplotlib.ticker import FuncFormatter
from plot_scales import apply_scale, display_values, intensity_norm
from ui.theme import BG, PANEL, TEXT, MUTED, TEAL, GOLD


def time_format(span_days, show_dates=False):
    """Tick label format for a time span (days): seconds for short windows, dates for long ones."""
    if span_days > 1.5 or (show_dates and span_days > .5):
        return '%m-%d %H:%M'
    return '%H:%M:%S' if span_days <= 15 / 1440 else '%H:%M'


class Charts(FigureCanvasQTAgg):
    def __init__(self):
        self.figure = Figure(figsize=(12, 7), facecolor=BG, layout='constrained')
        super().__init__(self.figure)
        self.grid = self.figure.add_gridspec(2, 2, height_ratios=[2.1, 1.1], width_ratios=[1, 1])
        self.spg = self.figure.add_subplot(self.grid[0, 0])
        self.accum = self.figure.add_subplot(self.grid[0, 1])
        self.count = self.figure.add_subplot(self.grid[1, :])
        self.dose = self.count.twinx()
        self.dose.patch.set_visible(False)
        self.dose.set_zorder(self.count.get_zorder() + 1)
        self.colorbar = None
        self.display_label = 'Recent recorded intervals'
        self.color_percentile = 100.0
        self.color_ceiling = None  # highest displayed cps/channel in this viewing session
        self.source_key = None
        self.local_time = False
        self.show_dates = False
        self.colormap = 'cividis'
        self.roi = None
        self.annotations = []  # (utc seconds, kind, text) from the run's notes
        self.energy_scale = 'log'
        self.counts_scale = 'linear'
        self.dose_scale = 'linear'
        self.spectrum_counts_scale = 'log'
        self.intensity_scale = 'log'
        self.scale_note = ''
        self.accumulated_row = None
        self.accumulated_error = None
        self.accumulated_status = ''
        self.series_visible = dict(counts=True, estimator=True, dose=True)
        self.series_artists = {}
        self.accum_view = None  # (energy limits, ordinate limits), display only
        self.empty()

    def set_source(self,key):
        if key!=self.source_key:
            self.source_key=key
            self.color_ceiling=None

    def set_focus_mode(self, enabled):
        """Give the two primary plots more height in the full-screen view."""
        self.grid.set_height_ratios([3.5, .65] if enabled else [2.1, 1.1])
        self.draw_idle()

    @staticmethod
    def style(ax, y_color=MUTED):
        ax.set_facecolor(PANEL)
        ax.tick_params(axis='x', colors=MUTED, labelsize=8)
        ax.tick_params(axis='y', colors=y_color, labelsize=8)
        for spine in ax.spines.values():
            spine.set_color('#314357')
        ax.xaxis.label.set_color(MUTED)
        ax.yaxis.label.set_color(y_color)
        ax.title.set_color(TEXT)
        ax.title.set_fontsize(10)

    def empty(self):
        if self.colorbar:
            self.colorbar.remove()
            self.colorbar = None
        self.series_artists = {}
        for ax, title in ((self.spg, 'Spectrogram'), (self.accum, 'Accumulated spectrum'),
                          (self.count, 'Count and dose rates'), (self.dose, '')):
            ax.clear()
            self.style(ax, TEAL if ax is self.count else GOLD if ax is self.dose else MUTED)
            if title:
                ax.set_title(title, loc='left')
                ax.text(.5, .5, 'Waiting for recorded data', transform=ax.transAxes,
                        ha='center', va='center', color=MUTED)
        self.dose.patch.set_visible(False)
        self._style_rate_axes()
        self.accum.set_xlabel('Energy (keV)')
        self.accum.set_ylabel('Accumulated counts / channel')
        self.spg.set_xlabel('Energy (keV)')
        self.spg.set_ylabel('UTC • time increases downward')
        self.count.set_xlabel('UTC')
        self.count.set_ylabel('Count rate (cps)', color=TEAL)
        self.dose.set_ylabel('Dose rate (µSv/h)', color=GOLD)
        apply_scale(self.spg, 'x', self.energy_scale)
        apply_scale(self.accum, 'x', self.energy_scale)
        apply_scale(self.accum, 'y', self.spectrum_counts_scale)
        self._pickable_axis_labels()
        apply_scale(self.count, 'y', self.counts_scale)
        apply_scale(self.dose, 'y', self.dose_scale)
        self.draw_idle()

    def _style_rate_axes(self):
        self.style(self.count, TEAL)
        self.style(self.dose, GOLD)
        self.dose.patch.set_visible(False)
        self.count.yaxis.set_label_position('left')
        self.count.yaxis.tick_left()
        self.dose.yaxis.set_label_position('right')
        self.dose.yaxis.tick_right()
        self.count.set_ylabel('Count rate (cps)', color=TEAL)
        self.dose.set_ylabel('Dose rate (µSv/h)', color=GOLD)
        self.dose.tick_params(axis='y', colors=GOLD, labelsize=8)
        self.dose.spines['right'].set_color(GOLD)
        self.count.spines['left'].set_color(TEAL)

    def set_series_visible(self, name, visible):
        """Show/hide one rate trace; the legend controls live outside the plot."""
        if name not in self.series_visible:
            raise ValueError(f'Unknown live series: {name}')
        self.series_visible[name] = bool(visible)
        for artist in self.series_artists.get(name, ()):
            artist.set_visible(bool(visible))
        self.draw_idle()

    def set_accumulated(self, row, error=None, status=''):
        """Set the raw-verified accumulated recording/range row for the live pane."""
        self.accumulated_row = row
        self.accumulated_error = error
        self.accumulated_status = status
        self._draw_accumulated()
        self.draw_idle()

    def _draw_accumulated(self):
        ax = self.accum
        ax.clear()
        self.style(ax)
        ax.set_xlabel('Energy (keV)')
        ax.set_ylabel('Accumulated counts / channel')
        row = self.accumulated_row
        if row is None:
            ax.set_title('Accumulated spectrum' + (' • unavailable' if self.accumulated_error else ''), loc='left')
            message = ('Accumulated spectrum unavailable: ' + self.accumulated_error
                       if self.accumulated_error else 'Waiting for verified recorded intervals')
            ax.text(.5, .5, message, transform=ax.transAxes,
                    ha='center', va='center', color=MUTED, wrap=True)
            apply_scale(ax, 'x', self.energy_scale)
            apply_scale(ax, 'y', self.spectrum_counts_scale)
            return
        _utc, _epochs, data, snapshot = row
        counts = data.get('counts_delta')
        if counts is None:
            calibration = snapshot['spectrum']['calibration_keV']
            channels = np.arange(len(snapshot['spectrum']['counts']))
            energy = calibration[0] + calibration[1] * channels + calibration[2] * channels**2
            ax.set_title('Accumulated spectrum', loc='left')
            ax.text(.5, .5, 'No complete recorded intervals yet', transform=ax.transAxes,
                    ha='center', va='center', color=MUTED)
            apply_scale(ax, 'x', self.energy_scale, energy)
            apply_scale(ax, 'y', self.spectrum_counts_scale)
            return
        counts = np.asarray(counts, dtype=np.int64)
        calibration = snapshot['spectrum']['calibration_keV']
        channels = np.arange(len(counts))
        energy = calibration[0] + calibration[1] * channels + calibration[2] * channels**2
        shown_energy = display_values(energy, self.energy_scale == 'log')
        shown_counts = display_values(counts, self.spectrum_counts_scale == 'log')
        if self.energy_scale == 'log':
            shown_counts = np.ma.masked_where(energy <= 0, shown_counts)
        if len(counts) > 1:
            ax.step(shown_energy[:-1], shown_counts[:-1], where='mid', color=TEAL, linewidth=1.1)
        if len(counts):
            ax.plot(shown_energy[-1:], shown_counts[-1:], marker='x', linestyle='none',
                    color=GOLD, markersize=4)
        collection = snapshot.get('collection') or {}
        intervals = collection.get('included_intervals')
        label = 'Accumulated' if collection.get('selection_rule', '').startswith('Complete recorded') and collection.get('requested_start_utc') is None else 'Selected range'
        suffix = f' • {intervals:,} complete intervals' if intervals is not None else ''
        status=f' • {self.accumulated_status}' if self.accumulated_status else ''
        ax.set_title(label + suffix + status, loc='left')
        if self.roi:
            lower, upper, include_terminal = self.roi
            mask = (energy >= lower) & (energy <= upper)
            if not include_terminal and len(mask):
                mask[-1] = False
            selected = int(counts[mask].sum())
            shown_lower = lower
            if self.energy_scale == 'log':
                positive = energy[energy > 0]
                if len(positive):
                    shown_lower = max(lower, float(positive.min()))
            if shown_lower < upper:
                ax.axvspan(shown_lower, upper, color=GOLD, alpha=.14)
            ax.set_title(label + suffix + status + f' • ROI {selected:,} counts', loc='left')
        apply_scale(ax, 'x', self.energy_scale, energy)
        apply_scale(ax, 'y', self.spectrum_counts_scale, counts)
        if self.accum_view:
            xlim, ylim = self.accum_view
            ax.set_xlim(*xlim)
            ax.set_ylim(*ylim)
        self._pickable_axis_labels()

    def _pickable_axis_labels(self):
        for label in (self.spg.xaxis.label, self.accum.xaxis.label,
                      self.accum.yaxis.label, self.count.yaxis.label,self.dose.yaxis.label):
            label.set_picker(5)

    def show_rows(self, rows, last_snapshot, mode):
        if self.colorbar:
            self.colorbar.remove()
            self.colorbar = None
        self.series_artists = {}
        for ax in (self.spg, self.accum, self.count, self.dose):
            ax.clear()
        self.style(self.spg)
        self.style(self.accum)
        self._style_rate_axes()
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
                centers = a0 + a1 * np.arange(channels) + a2 * np.arange(channels)**2
                mask = (centers >= self.roi[0]) & (centers <= self.roi[1])
                mask[-1] = mask[-1] and self.roi[2]
                roi_rates.append(float(np.asarray(delta)[mask].sum()) / d['device_duration_s'])
            else:
                roi_rates.append(np.nan)
            tb = s.get('telemetry_bin', {})
            coverage = tb.get('coverage_fraction', 0)
            dose_rates.append(tb.get('dose_rate_uSv_h') if coverage >= .999 and is_normal else np.nan)
            filtered.append(tb.get('device_count_rate_cps') if coverage >= .999 and is_normal else np.nan)
        span = (ends[-1] - starts[0]) if ends else 0.
        prefix = 'SIMULATED • ' if mode == 'demo' else ''
        self.spg.set_title(prefix + 'Spectrogram', loc='left')
        if ends and np.all(np.diff(energy) > 0):
            yedges = [starts[0]]
            cells = []
            for start, end, row in zip(starts, ends, matrix):
                if start > yedges[-1] + 1e-10:
                    cells.append(np.full(channels, np.nan))
                    yedges.append(start)
                cells.append(row)
                yedges.append(end)
            values = np.ma.masked_invalid(np.asarray(cells))
            values, norm = intensity_norm(values, self.intensity_scale, self.color_percentile)
            if self.color_percentile == 100:
                self.color_ceiling=max(norm.vmax,self.color_ceiling or norm.vmax)
                norm.vmax=self.color_ceiling
            if self.energy_scale == 'log':
                values = np.ma.masked_where(np.broadcast_to(energy[:-1] <= 0, values.shape), values)
            cmap = __import__('matplotlib').colormaps[self.colormap].with_extremes(bad=PANEL)
            mesh = self.spg.pcolormesh(energy, yedges, values, cmap=cmap, norm=norm,
                                      shading='flat', rasterized=True)
            # A horizontal bar leaves both top axes the same plot width.
            self.colorbar = self.figure.colorbar(mesh, ax=self.spg, orientation='horizontal',
                                                pad=.16, fraction=.06)
            limit = ('unclipped; upper limit held this session' if self.color_percentile == 100
                     else f'clipped at {self.color_percentile:g} percentile')
            self.colorbar.set_label('counts/s/channel • ' + self.intensity_scale + ' • ' + limit,
                                    color=MUTED, fontsize=8)
            self.colorbar.ax.tick_params(which='both', colors=MUTED, labelsize=8)
            self.spg.invert_yaxis()
            self.spg.yaxis.set_major_locator(mdates.AutoDateLocator(tz=dt.timezone.utc, minticks=3, maxticks=8))
            self.spg.yaxis.set_major_formatter(mdates.DateFormatter(time_format(span, self.show_dates), tz=dt.timezone.utc))
            line, = self.count.plot(ends, display_values(rates, self.counts_scale == 'log'), color=TEAL,
                                    linewidth=1.4, marker='.' if self.counts_scale == 'log' else None,
                                    markersize=3)
            self.series_artists['counts'] = [line]
            estimator, = self.count.plot(ends, display_values(filtered, self.counts_scale == 'log'),
                                         color=MUTED, linewidth=1., linestyle='--')
            self.series_artists['estimator'] = [estimator]
            if self.roi:
                roi_line, = self.count.plot(ends, display_values(roi_rates, self.counts_scale == 'log'),
                                            color=GOLD, linewidth=1.)
                self.series_artists['counts'].append(roi_line)
            dose_line, = self.dose.plot(ends, display_values(dose_rates,self.dose_scale=='log'),
                                        color=GOLD, linewidth=1.4)
            self.series_artists['dose'] = [dose_line]
            for name, artists in self.series_artists.items():
                for artist in artists:
                    artist.set_visible(self.series_visible[name])
        else:
            self.spg.text(.5, .5, 'Baseline saved; waiting for a complete interval', transform=self.spg.transAxes,
                          ha='center', color=MUTED)
        self._draw_accumulated()
        self.spg.set_xlabel('Energy (keV)')
        self.spg.set_ylabel('UTC • time increases downward')
        if self.local_time:
            self.spg.set_ylabel('Local time • UTC offset shown • time increases downward')
            local_format = time_format(span, self.show_dates) + ' %z'
            self.spg.yaxis.set_major_formatter(FuncFormatter(
                lambda value, pos: mdates.num2date(value, dt.timezone.utc).astimezone().strftime(local_format)))
        self.count.set_title('Count and dose rates • shared time axis', loc='left')
        for ax in (self.count, self.dose):
            ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=dt.timezone.utc, minticks=3, maxticks=7))
            ax.xaxis.set_major_formatter(mdates.DateFormatter(time_format(span, self.show_dates), tz=dt.timezone.utc))
            ax.set_xlabel('UTC')
            if self.local_time:
                ax.xaxis.set_major_formatter(FuncFormatter(
                    lambda value, pos: mdates.num2date(value, dt.timezone.utc).astimezone().strftime(local_format)))
                ax.set_xlabel('Local time • original timestamps are UTC')
        self.count.grid(alpha=.12, color=MUTED)
        self.dose.grid(False)
        if len(ends) == 1:
            self.count.set_xlim(starts[0], ends[0])
        apply_scale(self.spg, 'x', self.energy_scale, energy)
        apply_scale(self.count, 'y', self.counts_scale, [*rates, *filtered, *roi_rates])
        apply_scale(self.dose, 'y', self.dose_scale, dose_rates)
        if self.counts_scale == 'log' and ends and not display_values([*rates, *filtered, *roi_rates], True).count():
            self.count.text(.5, .5, 'No positive count rates\nChoose Linear to display zeros',
                            transform=self.count.transAxes, ha='center', color=MUTED)
        notes = []
        if self.energy_scale == 'log':
            notes.append('Log energy shows only bins with positive energy edges')
        if self.counts_scale == 'log':
            notes.append('Log count-rate axis hides zero/nonpositive values')
        if self.dose_scale == 'log':
            notes.append('Log dose-rate axis hides zero/nonpositive values')
        if self.intensity_scale == 'log':
            notes.append('Log intensity hides zero bins; Linear shows measured zeros')
        if self.color_percentile < 100:
            notes.append(f'Colors clipped at {self.color_percentile:g} percentile; apparent band widths can change')
        self.scale_note = ' • '.join(notes)
        self._draw_annotations(starts, ends)
        self._pickable_axis_labels()
        self.draw_idle()

    def _draw_annotations(self, starts, ends):
        """Dashed markers for notes inside the plotted span; the seal time (time zero) in gold."""
        if not ends or not self.annotations:
            return
        for utc, kind, text in self.annotations:
            x = mdates.date2num(dt.datetime.fromtimestamp(utc, dt.timezone.utc))
            if not starts[0] <= x <= ends[-1]:
                continue
            seal = kind == 'seal'
            style = dict(color=GOLD if seal else TEXT, linewidth=1.2 if seal else .8,
                         linestyle='--', alpha=.95 if seal else .55)
            self.spg.axhline(x, **style)
            self.count.axvline(x, **style)
            self.dose.axvline(x, **style)
            if seal:
                self.spg.text(1.005, x, 'seal', transform=self.spg.get_yaxis_transform(), color=GOLD,
                              fontsize=8, va='center', ha='left', clip_on=False)
