#!/usr/bin/env python3
"""Stable entrypoint for the deterministic orchestration harness.

This entry shim lives at the harness project root; the harness core lives under `src/`. It
puts `src/` on sys.path so the core's imports resolve, then runs one harness function
command through the composition root. Run it as
`python3 harness.py [--env-file <file>] <function> [flags]`, with `FRAMEWORK_DIR` (from the
environment, or from the layout file `--env-file` names) anchoring the framework whose
configuration is loaded.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from application import main  # noqa: E402


def _force_utf8_stdio() -> None:
    """Write reports and diagnostics as UTF-8 whatever the console codepage (Windows)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    _force_utf8_stdio()
    raise SystemExit(main())
