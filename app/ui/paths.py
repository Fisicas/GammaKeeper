"""Application root and the default run-data folder shared by window components."""
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def data_root(environ=os.environ):
    """Default folder for new runs (plan D34): never inside a release or the repository.

    GAMMAKEEPER_DATA_ROOT overrides it; otherwise Desktop\\Radiacode Data\\GammaKeeper Runs.
    """
    override = environ.get('GAMMAKEEPER_DATA_ROOT')
    return Path(override) if override else Path.home() / 'Desktop' / 'Radiacode Data' / 'GammaKeeper Runs'


DATA_ROOT = data_root()
# GUI preferences live beside the data, so upgrades and side-by-side releases share them.
SETTINGS_DIR = DATA_ROOT / '_settings'
# Derived analysis caches are reusable across app upgrades and disposable; raw runs remain authoritative.
ANALYSIS_CACHE_DIR = SETTINGS_DIR / 'analysis_cache'
