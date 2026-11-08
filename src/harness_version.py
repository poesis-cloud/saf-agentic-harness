"""The installed harness version, read from the `VERSION` file at the harness root.

Spec (core, Harness compatibility): the version the fail-fast load checks against the
framework's `require`, and the one function 0 stamps into every session it starts.
"""

from __future__ import annotations

from pathlib import Path

_VERSION_FILE = Path(__file__).resolve().parents[1] / "VERSION"


def read_harness_version(path: Path = _VERSION_FILE) -> str:
    """Answer the version the `VERSION` file names, surrounding whitespace stripped."""
    return path.read_text(encoding="utf-8").strip()


HARNESS_VERSION = read_harness_version()

__all__ = ["HARNESS_VERSION", "read_harness_version"]
