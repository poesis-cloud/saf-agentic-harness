#!/usr/bin/env python3
"""The `github-copilot-cli` hook entry: host event JSON on stdin, decision JSON on stdout.

    <python> adapter.py hook --event <camelCaseEvent> [--agent <slug>] [--framework-dir <dir>]

The installer renders every hook entry to run THIS file with an explicit interpreter, so
no shell (and no bash on Windows) sits between the runtime and the adapter.

Bootstrap failure (spec, as for every adapter): any failure escaping the hook handling — a
missing or non-directory framework root, an unreadable or malformed `.env`, an off-contract
binding, an unreadable payload, an unexpected crash — answers exit 2 with a stderr
diagnostic, never an exit the runtime fails open on. The Copilot CLI runtime turns exit 2
into a DENY at `preToolUse` and into a user-visible warning at every other event. A
pass-through on failure is never chosen, even for a session without a record: a broken
bootstrap is exactly what keeps H0 from creating that record.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence, TextIO

ADAPTER_DIR = Path(__file__).resolve().parent
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from copilot_cli_adapter import renderer  # noqa: E402
from copilot_cli_adapter.binding import load_tool_binding  # noqa: E402
from copilot_cli_adapter.core import ADAPTER_ID, EVENTS, Adapter  # noqa: E402
from copilot_cli_adapter.invocation import HarnessInvocation  # noqa: E402
from copilot_cli_adapter.layout import (  # noqa: E402
    resolve_layout,
    resolve_state_dir,
)
from copilot_cli_adapter.payload import HookInput  # noqa: E402
from copilot_cli_adapter.renderer import ContextRenderer  # noqa: E402
from copilot_cli_adapter.runner import SubprocessCommandRunner  # noqa: E402
from copilot_cli_adapter.tracker import SessionTracker  # noqa: E402

HARNESS_ENTRYPOINT = ADAPTER_DIR.parents[1] / "harness.py"
EXIT_FAIL_CLOSED = 2


def build_adapter(
    framework_dir: str | None, workspace_dir: str | None = None
) -> tuple[Adapter, Any]:
    """Build the adapter and its binding from this directory and the framework layout."""
    layout = resolve_layout(framework_dir, workspace_dir=workspace_dir)
    binding = load_tool_binding(ADAPTER_DIR)
    adapter = Adapter(
        binding=binding,
        tracker=SessionTracker(resolve_state_dir(ADAPTER_ID)),
        context_renderer=ContextRenderer(
            instructions_dir=layout.resolve("FRAMEWORK_INSTRUCTIONS_DIR"),
            skills_dir=layout.resolve("FRAMEWORK_SKILLS_DIR"),
        ),
        runner=SubprocessCommandRunner(
            harness_entrypoint=HARNESS_ENTRYPOINT,
            layout_environment=layout.environment,
        ),
        workspace_dir=str(layout.resolve("FRAMEWORK_WORKSPACE_DIR")),
        invocation=HarnessInvocation(
            python=sys.executable,
            harness=str(HARNESS_ENTRYPOINT),
            windows=os.name == "nt",
            framework_dir=str(layout.framework_dir),
            workspace_dir=str(layout.resolve("FRAMEWORK_WORKSPACE_DIR")),
        ),
    )
    return adapter, binding


def run_hook(
    argv: Sequence[str],
    stdin: BinaryIO,
    stdout: TextIO,
    stderr: TextIO,
    build: Callable[[str | None, str | None], tuple[Adapter, Any]] = build_adapter,
) -> int:
    arguments = _parse_arguments(argv)
    try:
        raw = stdin.read()
        payload = json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}
        if not isinstance(payload, Mapping):
            raise ValueError("the hook payload is not a JSON object")
        adapter, binding = build(arguments.framework_dir, arguments.workspace_dir)
        decision = adapter.handle(
            HookInput(
                event=arguments.event,
                payload=payload,
                binding=binding,
                scoping_agent=arguments.agent,
            )
        )
    except Exception as failure:  # noqa: BLE001 - the entry must never crash open
        print(
            f"[{ADAPTER_ID}] {arguments.event}: the hook failed — failing closed: "
            f"{type(failure).__name__}: {failure}",
            file=stderr,
        )
        stderr.flush()
        return EXIT_FAIL_CLOSED
    output = renderer.render(decision)
    if output:
        stdout.write(output + "\n")
        stdout.flush()
    return 0


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="adapter.py")
    parser.add_argument("command", choices=("hook",))
    parser.add_argument("--event", required=True, choices=EVENTS)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--framework-dir", default=None)
    parser.add_argument("--workspace-dir", default=None)
    return parser.parse_args(list(argv))


def _force_utf8_stdio() -> None:
    """Answer UTF-8 whatever the console codepage: scoped hooks carry no PYTHONUTF8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_stdio()
    return run_hook(
        argv=list(argv) if argv is not None else sys.argv[1:],
        stdin=sys.stdin.buffer,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
