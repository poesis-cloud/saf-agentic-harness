"""Fixtures for the `github-copilot` adapter PROCESS suite.

Host event JSON on stdin → `<python> adapters/github-copilot/adapter.py hook --event <e>`
(exactly what the installer renders as an `exec` entry) → the scripted stub `harness.py` →
decision JSON on stdout. Nothing here imports the adapter.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[2]
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
