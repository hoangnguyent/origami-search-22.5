"""Compatibility shim for the compiled pybind11 extension.

This module re-exports the compiled extension as `src.engine.math225_core` so
existing imports such as `from src.engine.math225_core import Vertex4D` work.
"""

from pathlib import Path
import sys

_MODULE_DIR = Path(__file__).resolve().parent.parent
if str(_MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULE_DIR))

from math225_core import *  # noqa: F401,F403
