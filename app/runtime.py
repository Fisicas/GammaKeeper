"""Use the configured science environment for every helper process."""
import os
import sys
from pathlib import Path

def _running_python():
    """python.exe beside the interpreter running GammaKeeper (the GUI may run under pythonw.exe)."""
    here = Path(sys.executable)
    console = here.with_name('python.exe')
    return console if console.is_file() else here


# GAMMAKEEPER_SCIENCE_PYTHON overrides (RADIALOG_SCIENCE_PYTHON is still honored); by default helpers
# run with the same interpreter as the GUI.
PYTHON = Path(os.environ.get('GAMMAKEEPER_SCIENCE_PYTHON') or os.environ.get('RADIALOG_SCIENCE_PYTHON')
              or _running_python())
PYTHONW = PYTHON.with_name('pythonw.exe')

def python_command():
    if not PYTHON.is_file():
        raise FileNotFoundError(f'Configured science interpreter is unavailable: {PYTHON}')
    return str(PYTHON)
