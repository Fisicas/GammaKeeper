"""Compatibility entry point for the RadiaLog/GammaKeeper window.

The window now lives in ui/. Launchers start this file, and tests and gui_smoke.py
import these names from it."""
import bootstrap  # noqa: F401 -- adds app-local dependencies/ to sys.path
import gui_preferences
from ui.theme import BG, PANEL, TEXT, MUTED, TEAL, GOLD
from ui.charts import Charts
from ui.setup_panel import SettingsSpinBox
from ui.main_window import ROOT, Window, main

__all__ = ['gui_preferences', 'BG', 'PANEL', 'TEXT', 'MUTED', 'TEAL', 'GOLD', 'Charts', 'SettingsSpinBox',
           'ROOT', 'Window', 'main']


if __name__ == '__main__':
    raise SystemExit(main())
