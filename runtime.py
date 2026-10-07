"""Use the configured science environment for every helper process."""
import os
from pathlib import Path

PYTHON = Path(os.environ.get('RADIALOG_SCIENCE_PYTHON', r'C:\Users\nelso\.venvs\codex-science-v1\Scripts\python.exe'))
PYTHONW = PYTHON.with_name('pythonw.exe')

def python_command():
    if not PYTHON.is_file():
        raise FileNotFoundError(f'Configured science interpreter is unavailable: {PYTHON}')
    return str(PYTHON)
