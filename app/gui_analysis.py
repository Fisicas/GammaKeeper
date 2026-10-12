"""Compatibility re-exports: the analysis views now live in ui/."""
from ui.tasks import PreviewSignals, PreviewTask, AccumulationTask
from ui.spectrum_tab import SpectrumCanvas
from ui.analysis_panel import AnalysisUI

__all__ = ['PreviewSignals', 'PreviewTask', 'AccumulationTask', 'SpectrumCanvas', 'AnalysisUI']
