#!/usr/bin/env python3
"""The `github-copilot-cli` hook entry: host event JSON on stdin, decision JSON on stdout.

    <python> adapter.py hook --event <camelCaseEvent> [--agent <slug>] [--framework-dir <dir>]

The installer renders every hook entry to run THIS file with an explicit interpreter, so
no shell (and no bash on Windows) sits between the runtime and the adapter. Fail closed:
at `preToolUse`, any adapter failure DENIES a session that is (or may be) governed — only a
readable payload with no session record passes, so a broken install cannot block tool use in
every folder the app opens; at every other event a failure is a diagnostic on stderr and a
pass-through (those events cannot veto anyway).
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
from copilot_cli_adapter.core import ADAPTER_ID, EVENTS, PRE_TOOL_USE, Adapter  # noqa: E402
from copilot_cli_adapter.invocation import HarnessInvocation  # noqa: E402
from copilot_cli_adapter.layout import (  # noqa: E402
    resolve_layout,
    resolve_state_dir,
)
from copilot_cli_adapter.payload import HookInput, sanitize_identifier  # noqa: E402
from copilot_cli_adapter.renderer import ContextRenderer  # noqa: E402
from copilot_cli_adapter.runner import SubprocessCommandRunner  # noqa: E402
from copilot_cli_adapter.tracker import SessionTracker  # noqa: E402

HARNESS_ENTRYPOINT = ADAPTER_DIR.parents[1] / "harness.py"
# The observed host session key, read without the binding (which may be what failed).
_HOST_SESSION_KEY = "sessionId"


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
            environment=layout.absolute_environment,
        ),
    )
    return adapter, binding


def session_may_be_governed(payload: Any) -> bool:
    """False only when the payload is readable AND no session record exists for it."""
    try:
        if not isinstance(payload, Mapping):
            return True
        raw = payload.get(_HOST_SESSION_KEY)
        if not isinstance(raw, str) or not raw:
            return True
        tracker = SessionTracker(resolve_state_dir(ADAPTER_ID))
        return tracker.read(sanitize_identifier(raw)) is not None
    except Exception:  # noqa: BLE001 - any doubt is "governed": deny
        return True


def run_hook(
    argv: Sequence[str],
    stdin: BinaryIO,
    stdout: TextIO,
    stderr: TextIO,
    build: Callable[[str | None, str | None], tuple[Adapter, Any]] = build_adapter,
    may_be_governed: Callable[[Any], bool] | None = None,
) -> int:
    may_be_governed = may_be_governed or session_may_be_governed
    arguments = _parse_arguments(argv)
    payload: Any = None
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
        print(f"[{ADAPTER_ID}] {arguments.event}: {type(failure).__name__}: {failure}", file=stderr)
        if arguments.event != PRE_TOOL_USE:
            return 0
        if not may_be_governed(payload):
            # The plugin's hooks fire in EVERY folder the app opens: a broken install must not
            # take down tool use in sessions the harness never governed.
            return 0
        decision = renderer.deny(
            [f"the SAFe agentic harness adapter failed ({type(failure).__name__}); denied"]
        )
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


def main(argv: Sequence[str] | None = None) -> int:
    return run_hook(
        argv=list(argv) if argv is not None else sys.argv[1:],
        stdin=sys.stdin.buffer,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
