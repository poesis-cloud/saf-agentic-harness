#!/usr/bin/env python3
"""The `github-copilot` hook entry: host event JSON on stdin, decision JSON on stdout.

    <python> adapter.py hook --event <camelCaseEvent> [--agent <slug>] [--framework-dir <dir>]
        [--workspace-dir <dir>] [--workspace-root <slug>] [--layout FRAMEWORK_<X>_DIR=<path>]...

In plugin mode the launcher (`launcher.py`) runs this entry with the workspace it resolved
from the session folder's marker and that marker's `root` (`--workspace-root`).

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
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence, TextIO

import yaml

ADAPTER_DIR = Path(__file__).resolve().parent
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from github_copilot_adapter import renderer  # noqa: E402
from github_copilot_adapter.binding import load_tool_binding  # noqa: E402
from github_copilot_adapter.core import ADAPTER_ID, EVENTS, Adapter  # noqa: E402
from github_copilot_adapter.invocation import HarnessInvocation  # noqa: E402
from github_copilot_adapter.layout import (  # noqa: E402
    LAYOUT_VARIABLES,
    resolve_layout,
    resolve_state_dir,
)
from github_copilot_adapter.payload import HookInput  # noqa: E402
from github_copilot_adapter.renderer import ContextRenderer  # noqa: E402
from github_copilot_adapter.runner import SubprocessCommandRunner  # noqa: E402
from github_copilot_adapter.tracker import SessionTracker  # noqa: E402

HARNESS_ENTRYPOINT = ADAPTER_DIR.parents[1] / "harness.py"
HARNESS_VERSION_FILE = ADAPTER_DIR.parents[1] / "VERSION"
EXIT_FAIL_CLOSED = 2
# Harness calls must end this far before the registered `timeoutSec` (the larger of both).
DEADLINE_MARGIN_SECONDS = 5.0
DEADLINE_MARGIN_RATIO = 0.25


def hook_deadline(event: str, scoped: bool, started: float) -> float:
    """The instant every harness call of this firing must finish by: well inside the
    `timeoutSec` the event is registered with, since the runtime fails a timed-out
    `preToolUse` OPEN. The registration is the installer's own source of truth."""
    registrations = [("hooks.yaml", "hooks"), ("agent-hooks.yaml", "agentHooks")]
    if scoped:
        registrations.reverse()
    for source, key in registrations:
        registered = yaml.safe_load((ADAPTER_DIR / source).read_text(encoding="utf-8"))
        entry = ((registered or {}).get(key) or {}).get(event)
        if isinstance(entry, Mapping):
            timeout = entry.get("timeoutSec")
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                raise ValueError(f"{source} gives '{event}' no positive timeoutSec")
            margin = max(DEADLINE_MARGIN_SECONDS, timeout * DEADLINE_MARGIN_RATIO)
            return started + timeout - margin
    raise ValueError(f"no hook registration names the event '{event}'")


def build_adapter(
    framework_dir: str | None,
    workspace_dir: str | None = None,
    layout_overrides: Mapping[str, str] | None = None,
    deadline: float | None = None,
) -> tuple[Adapter, Any]:
    """Build the adapter and its binding from this directory and the framework layout."""
    layout = resolve_layout(
        framework_dir, workspace_dir=workspace_dir, overrides=layout_overrides
    )
    binding = load_tool_binding(ADAPTER_DIR, framework_dir=layout.framework_dir)
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
            deadline=deadline,
        ),
        workspace_dir=str(layout.resolve("FRAMEWORK_WORKSPACE_DIR")),
        invocation=HarnessInvocation(
            python=sys.executable,
            harness=str(HARNESS_ENTRYPOINT),
            windows=os.name == "nt",
            framework_dir=str(layout.framework_dir),
            workspace_dir=str(layout.resolve("FRAMEWORK_WORKSPACE_DIR")),
        ),
        harness_version=read_harness_version(),
    )
    return adapter, binding


def read_harness_version() -> str:
    """The installed harness version (`VERSION` at the harness root)."""
    return HARNESS_VERSION_FILE.read_text(encoding="utf-8").strip()


def run_hook(
    argv: Sequence[str],
    stdin: BinaryIO,
    stdout: TextIO,
    stderr: TextIO,
    build: Callable[
        [str | None, str | None, Mapping[str, str], float], tuple[Adapter, Any]
    ] = build_adapter,
) -> int:
    started = time.monotonic()
    arguments = _parse_arguments(argv)
    try:
        overrides = _read_layout_overrides(arguments.layout)
        deadline = hook_deadline(arguments.event, arguments.agent is not None, started)
        if arguments.workspace_root is not None:
            require_workspace_root(arguments.framework_dir, overrides, arguments.workspace_root)
        raw = stdin.read()
        payload = json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}
        if not isinstance(payload, Mapping):
            raise ValueError("the hook payload is not a JSON object")
        adapter, binding = build(
            arguments.framework_dir, arguments.workspace_dir, overrides, deadline
        )
        hook = HookInput(
            event=arguments.event,
            payload=payload,
            binding=binding,
            scoping_agent=arguments.agent,
        )
        # The session's workspace was fixed when its record was created (adapter spec,
        # Session identity): a later firing never re-resolves it.
        recorded = adapter.recorded_workspace(hook)
        if recorded and recorded != adapter.workspace_dir:
            adapter, binding = build(arguments.framework_dir, recorded, overrides, deadline)
            hook = HookInput(
                event=arguments.event,
                payload=payload,
                binding=binding,
                scoping_agent=arguments.agent,
            )
        decision = adapter.handle(hook)
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
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--layout", action="append", default=[], metavar="FRAMEWORK_X_DIR=PATH")
    return parser.parse_args(list(argv))


def require_workspace_root(
    framework_dir: str | None, overrides: Mapping[str, str], root: str
) -> None:
    """The marker's `root` must name a top-level node of the framework's workspace layout.

    Adapter spec, Workspace resolution: otherwise a configuration error, failing closed.
    """
    layout = resolve_layout(framework_dir, overrides=overrides)
    source = layout.framework_dir / "conf" / "workspace.conf.yaml"
    loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    nodes = loaded.get("nodes") if isinstance(loaded, Mapping) else None
    slugs = {
        node.get("slug") for node in nodes or () if isinstance(node, Mapping)
    }
    if root not in slugs:
        raise ValueError(
            f"the SAF workspace marker's root '{root}' is not a top-level node of {source}"
        )


def _read_layout_overrides(pairs: Sequence[str]) -> dict[str, str]:
    """`--layout VAR=VALUE` pairs: the install-time layout a scoped hook (no `env`) carries."""
    overrides: dict[str, str] = {}
    for pair in pairs:
        variable, separator, value = pair.partition("=")
        if not separator or variable not in LAYOUT_VARIABLES or not value:
            raise ValueError(f"--layout {pair!r} is not FRAMEWORK_<X>_DIR=<path>")
        overrides[variable] = value
    return overrides


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
