"""Fixtures for the real-harness adapter suite: host events → adapter → the REAL harness.

Nothing here stands in for `harness.py`: a copy of the
harness tree (`adapters/`, `src/`, `contracts/`, `harness.py`) runs every function against a
real framework and a real Git workspace, so the suite observes what the step transaction
leaves in the workspace repository itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parents[2]
for _helpers in (_TESTS_DIR, _TESTS_DIR / "functional"):
    if str(_helpers) not in sys.path:
        sys.path.insert(0, str(_helpers))
