#!/usr/bin/env python3
"""Stable entrypoint for the deterministic orchestration harness.

This entry shim lives at the harness project root; the harness core lives under `src/`. It
puts `src/` on sys.path so the core's imports resolve, then runs one harness function
command through the composition root. Run it as `python3 harness.py <function> [flags]`,
with `FRAMEWORK_DIR` anchoring the framework whose configuration is loaded.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from application import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
