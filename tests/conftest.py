"""Pytest import shim matching the repository entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

sys.path.insert(0, str(SRC_ROOT))
# Shared test helpers (e.g. `posix_shell`) live beside this conftest.
sys.path.insert(0, str(Path(__file__).resolve().parent))
