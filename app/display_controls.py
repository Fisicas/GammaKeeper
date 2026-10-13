"""Linked display controls for spectra and time-series views."""
from PySide6 import QtWidgets as Q


def selector(default='linear'):
    widget=Q.QComboBox()
    widget.addItem('Linear','linear');widget.addItem('Logarithmic','log')
    widget.setCurrentIndex(1 if default=='log' else 0)
    widget.setToolTip('Display scale only. Logarithmic views omit nonpositive values; raw counts and exports are unchanged.')
    return widget


# Spectrogram color maps. Viridis is the default: perceptually uniform, color-blind safe,
# readable in grayscale, and its darkest color stays distinct from the dark plot panel.
COLORMAPS = [('Viridis', 'viridis'), ('Cividis (color-blind optimized)', 'cividis'), ('Inferno', 'inferno'),
             ('Magma', 'magma'), ('Plasma', 'plasma'), ('Turbo (rainbow; not perceptually uniform)', 'turbo'),
             ('Grayscale', 'gray')]
DEFAULT_COLORMAP = 'viridis'


def colormap_selector():
    widget=Q.QComboBox()
    for text,name in COLORMAPS:widget.addItem(text,name)
    widget.setCurrentIndex(widget.findData(DEFAULT_COLORMAP))
    widget.setToolTip('Spectrogram color map (display only).')
    return widget


def controls(window, spectrum=False):
    row=Q.QHBoxLayout()
    energy=selector('log')
    counts=selector('log' if spectrum else 'linear')
    row.addWidget(Q.QLabel('Energy axis'));row.addWidget(energy)
    row.addWidget(Q.QLabel('Counts axis' if spectrum else 'Count-rate axis'));row.addWidget(counts)
    if spectrum:
        window.spectrum_energy_axis=energy;window.spectrum_tab_counts_axis=counts
    else:
        window.energy_axis=energy;window.counts_axis=counts
        window.spectrum_counts_axis=selector('log')
        row.addWidget(Q.QLabel('Spectrum counts'));row.addWidget(window.spectrum_counts_axis)
        window.intensity_scale=selector('log')
        row.addWidget(Q.QLabel('Spectrogram intensity'));row.addWidget(window.intensity_scale)
        window.spectrogram_colormap=colormap_selector()
        row.addWidget(Q.QLabel('Colors'));row.addWidget(window.spectrogram_colormap)
    row.addStretch()
    return row


def connect_controls(window):
    for original,mirror in ((window.energy_axis,window.spectrum_energy_axis),
                            (window.spectrum_counts_axis,window.spectrum_tab_counts_axis)):
        original.currentIndexChanged.connect(mirror.setCurrentIndex)
        mirror.currentIndexChanged.connect(original.setCurrentIndex)
    for widget in (window.energy_axis,window.counts_axis,window.spectrum_counts_axis,
                   window.spectrum_tab_counts_axis,window.intensity_scale,window.spectrogram_colormap):
        widget.currentIndexChanged.connect(window.display_scales_changed)


def preferences(window):
    return dict(energy=window.energy_axis.currentData(),counts=window.counts_axis.currentData(),
                spectrum_counts=window.spectrum_counts_axis.currentData(),
                intensity=window.intensity_scale.currentData())


def restore(window,values):
    values=values if isinstance(values,dict) else {}
    for name,widgets,default in (('energy',(window.energy_axis,window.spectrum_energy_axis),'log'),
                                 ('counts',(window.counts_axis,),'linear'),
                                 ('spectrum_counts',(window.spectrum_counts_axis,window.spectrum_tab_counts_axis),'log'),
                                 ('intensity',(window.intensity_scale,),'log')):
        value=values.get(name,default)
        if value not in ('linear','log'):value=default
        for widget in widgets:
            blocked=widget.blockSignals(True)
            widget.setCurrentIndex(widget.findData(value))
            widget.blockSignals(blocked)
    window.display_scales_changed()
