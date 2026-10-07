"""Add application-local dependencies without altering the shared environment."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'dependencies'))
