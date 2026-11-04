"""Fixtures for the `github-copilot-cli` adapter unit suite (helpers live in `cli_rig`)."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from cli_rig import binding, rig  # noqa: E402,F401
