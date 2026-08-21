#!/usr/bin/env python3
"""A scripted stand-in for `harness.py` — the adapter's ONE seam into the harness core.

Adapter spec I15: the adapter's only dependency is the command API, so that command API
is the only thing this suite has to stand in for. The `framework` fixture copies this file
into its tmp framework root as `harness.py`, and the adapter's own
`SubprocessCommandRunner` really spawns it: same argv shape, same stdout-report protocol,
same exit-code protocol — with no framework configuration, no workflow catalog and no
harness core involved at all.

Two environment variables drive it, both set by the fixture:

- `HARNESS_STUB_SCRIPT` — a JSON object mapping a function name to `{"report": <the one
  report to print>}`, `{"reports": [<one report per successive call>]}` (the last entry is
  reused once exhausted, which is what lets H3's per-path fan-out be answered path by
  path), or `{"failure": <the stderr text>}`;
- `HARNESS_STUB_JOURNAL` — a JSONL file this script appends one line to per invocation,
  so a test can assert WHICH functions the adapter invoked, in which order, with which
  flags (and, for pass-through, that it invoked none at all).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

_SCRIPT_VARIABLE = "HARNESS_STUB_SCRIPT"
_JOURNAL_VARIABLE = "HARNESS_STUB_JOURNAL"


def main(argv: Sequence[str]) -> int:
    """Answer the scripted report for this function, journaling the invocation first."""
    function = argv[0] if argv else ""
    call_index = _journal(function, argv[1:])
    answer = _read_script().get(function)
    if answer is None:
        print(f"stub harness: no scripted answer for '{function}'", file=sys.stderr)
        return 1
    failure = answer.get("failure")
    if failure is not None:
        print(str(failure), file=sys.stderr)
        return 1
    print(json.dumps(_select_report(answer, call_index), sort_keys=True))
    return 0


def _select_report(answer: Mapping[str, Any], call_index: int) -> Any:
    """Answer this call's report — the last scripted one once the queue is exhausted."""
    reports = answer.get("reports")
    if reports is None:
        return answer["report"]
    return reports[min(call_index, len(reports) - 1)]


def _read_script() -> Mapping[str, Mapping[str, Any]]:
    script_path = Path(os.environ[_SCRIPT_VARIABLE])
    return json.loads(script_path.read_text(encoding="utf-8"))


def _journal(function: str, flags: Sequence[str]) -> int:
    """Record this invocation and answer how many calls of this function preceded it."""
    journal_path = Path(os.environ[_JOURNAL_VARIABLE])
    previous = journal_path.read_text(encoding="utf-8").splitlines()
    entry = json.dumps({"function": function, "argv": list(flags)}, sort_keys=True)
    with journal_path.open("a", encoding="utf-8") as journal:
        journal.write(f"{entry}\n")
    return sum(1 for line in previous if line and json.loads(line)["function"] == function)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
