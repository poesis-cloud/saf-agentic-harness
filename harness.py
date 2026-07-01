#!/usr/bin/env python3
"""Stable entrypoint for the deterministic orchestration harness.

This entry shim lives at the harness project root; the importable `harness` package lives under
`src/`. It puts `src/` on sys.path so `harness.*` imports resolve, then runs the CLI. Run it from
the harness dir as `python3 harness.py <command>`, or from the framework root as
`python3 harness/harness.py <command>`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
