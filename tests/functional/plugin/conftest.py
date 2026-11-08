"""Fixtures for the plugin-mode suite: the rendered harness plugin, run as the host runs it."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[2]
for _helpers in (
    _TESTS_DIR,
    _TESTS_DIR / "functional",
    _TESTS_DIR / "functional" / "adapter_real_harness",
):
    if str(_helpers) not in sys.path:
        sys.path.insert(0, str(_helpers))
