"""Fixtures for the `github-copilot-cli` adapter PROCESS suite.

Host event JSON on stdin → `<python> adapters/github-copilot-cli/adapter.py hook --event <e>`
(exactly what the installer renders as an `exec` entry) → the scripted stub `harness.py` →
decision JSON on stdout. Nothing here imports the adapter.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[2]
for _helpers in (_TESTS_DIR / "unit" / "adapter", _TESTS_DIR / "functional" / "adapter"):
    if str(_helpers) not in sys.path:
        sys.path.insert(0, str(_helpers))
