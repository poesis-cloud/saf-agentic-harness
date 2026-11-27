#!/usr/bin/env python3
"""The `claude-code` hook entry: host event JSON on stdin, decision JSON on stdout.

    <python> adapter.py hook --event <Event> [--framework-dir <dir>] [--workspace-dir <dir>]
        [--workspace-root <slug>]

In plugin mode the launcher (`launcher.py`) runs this entry with the framework root the
registry records, the workspace it resolved from the session folder's marker, and that
marker's `root` (`--workspace-root`).

Bootstrap failure (spec, as for every adapter): any failure escaping the hook handling — a
missing or non-directory framework root, an unreadable or malformed `.env`, an off-contract
binding, an unreadable payload, an unexpected crash — fails closed with a stderr diagnostic.
Claude Code honors only exit 2 as a block (any other non-zero exit is a non-blocking error,
i.e. a fail OPEN), so the gating event (`PreToolUse`) answers exit 2. Every other event
answers exit 1: at `Stop`/`SubagentStop` an exit 2 would keep the agent running, and at
`UserPromptSubmit` it would erase the user's prompt — the gating event already denies every
tool call of a session whose bootstrap is broken.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence, TextIO

ADAPTER_DIR = Path(__file__).resolve().parent
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))


def bootstrap_exit(argv: Sequence[str]) -> int:
    """The exit of a firing that failed before any hook handling (stdlib only): block where
    the runtime can veto (`PreToolUse`, exit 2), warn everywhere else (exit 1)."""
    arguments = list(argv)
    event = next((arguments[i + 1] for i, a in enumerate(arguments[:-1]) if a == "--event"), "")
    return 2 if event == "PreToolUse" else 1


try:
    import yaml

    from claude_code_adapter import renderer
    from claude_code_adapter.core import ADAPTER_ID, EVENTS, GATING_EVENT, Adapter
    from claude_code_adapter.renderer import ContextRenderer
    from saf_host_adapter.binding import load_tool_binding
    from saf_host_adapter.invocation import HarnessInvocation
    from saf_host_adapter.layout import resolve_layout, resolve_state_dir
    from saf_host_adapter.payload import HookInput
    from saf_host_adapter.runner import SubprocessCommandRunner
    from saf_host_adapter.tracker import SessionTracker
except Exception as _import_failure:  # noqa: BLE001 - a broken runtime must never fail open
    if __name__ != "__main__":
        raise
    print(
        f"[claude-code] the adapter could not load — failing closed: "
        f"{type(_import_failure).__name__}: {_import_failure}",
        file=sys.stderr,
    )
    raise SystemExit(bootstrap_exit(sys.argv[1:])) from _import_failure

HARNESS_ENTRYPOINT = ADAPTER_DIR.parents[1] / "harness.py"
HARNESS_VERSION_FILE = ADAPTER_DIR.parents[1] / "VERSION"
EXIT_BLOCK = 2
EXIT_WARN = 1
# Harness calls must end this far before the registered `timeoutSec` (the larger of both).
DEADLINE_MARGIN_SECONDS = 5.0
DEADLINE_MARGIN_RATIO = 0.25
# Claude Code has no batched tool-call shape: the single-call probes gate every firing.
GATING_PROBES = (
    ("writeTools", "no write would ever be authorized (H3) or validated (H5)"),
    ("pathKeys", "no write target could be extracted — every write would be denied"),
    ("toolKeys", "no tool call would be named"),
    ("inputKeys", "no tool call's arguments could be read"),
    ("hostSessionKeys", "no event could be correlated to its session"),
    ("hostRootSessionKeys", "no subagent could be correlated to its conversation"),
    ("hostTurnAgentKeys", "no orchestrator turn would ever open (H0)"),
    ("dispatchTools", "no step dispatch would be gated (H2)"),
    ("dispatchAgentKeys", "no started subagent could be correlated to its dispatch (H1)"),
    ("mediatedCommandTools", "no harness command would be mediated (H4)"),
)


def failure_exit(event: str) -> int:
    """Fail closed where the runtime can veto (exit 2), warn everywhere else (exit 1)."""
    return EXIT_BLOCK if event == GATING_EVENT else EXIT_WARN


def hook_deadline(event: str, started: float) -> float:
    """The instant every harness call of this firing must finish by: well inside the
    `timeoutSec` the event is registered with, since the runtime fails a timed-out
    `PreToolUse` OPEN. The registration is the plugin renderer's own source of truth."""
    registered = yaml.safe_load((ADAPTER_DIR / "hooks.yaml").read_text(encoding="utf-8"))
    entry = ((registered or {}).get("hooks") or {}).get(event)
    if not isinstance(entry, Mapping):
        raise ValueError(f"no hook registration names the event '{event}'")
    timeout = entry.get("timeoutSec")
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError(f"hooks.yaml gives '{event}' no positive timeoutSec")
    margin = max(DEADLINE_MARGIN_SECONDS, timeout * DEADLINE_MARGIN_RATIO)
    return started + timeout - margin


def build_adapter(
    framework_dir: str | None,
    workspace_dir: str | None = None,
    deadline: float | None = None,
) -> tuple[Adapter, Any]:
    """Build the adapter and its binding from this directory and the framework layout."""
    layout = resolve_layout(framework_dir, workspace_dir=workspace_dir)
    binding = load_tool_binding(
        ADAPTER_DIR, framework_dir=layout.framework_dir, gating_probes=GATING_PROBES
    )
    workspace = str(layout.resolve("FRAMEWORK_WORKSPACE_DIR"))
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
        workspace_dir=workspace,
        # Claude Code's shell tool is `Bash` on every OS (Git Bash on Windows): the request
        # line is rendered and parsed with POSIX quoting.
        invocation=HarnessInvocation(
            python=sys.executable,
            harness=str(HARNESS_ENTRYPOINT),
            windows=False,
            framework_dir=str(layout.framework_dir),
            workspace_dir=workspace,
        ),
        harness_version=read_harness_version(),
    )
    return adapter, binding


def read_harness_version() -> str:
    """The installed harness version (`VERSION` at the harness root)."""
    return HARNESS_VERSION_FILE.read_text(encoding="utf-8").strip()


def require_workspace_root(framework_dir: str | None, root: str) -> None:
    """The marker's `root` must name a top-level node of the framework's workspace layout.

    Adapter spec, Workspace resolution: otherwise a configuration error, failing closed.
    """
    layout = resolve_layout(framework_dir)
    source = layout.framework_dir / "conf" / "workspace.conf.yaml"
    loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    nodes = loaded.get("nodes") if isinstance(loaded, Mapping) else None
    slugs = {node.get("slug") for node in nodes or () if isinstance(node, Mapping)}
    if root not in slugs:
        raise ValueError(
            f"the SAF workspace marker's root '{root}' is not a top-level node of {source}"
        )


def _hook(event: str, payload: Mapping[str, Any], binding: Any) -> HookInput:
    # Claude Code's subagent start names no dispatcher, and the conversation's session id
    # is shared by every subagent: never take it for the dispatcher.
    return HookInput(
        event=event, payload=payload, binding=binding, dispatcher_from_session=False
    )


def run_hook(
    argv: Sequence[str],
    stdin: BinaryIO,
    stdout: TextIO,
    stderr: TextIO,
    build: Callable[[str | None, str | None, float], tuple[Adapter, Any]] = build_adapter,
) -> int:
    started = time.monotonic()
    arguments = _parse_arguments(argv)
    try:
        deadline = hook_deadline(arguments.event, started)
        if arguments.workspace_root is not None:
            require_workspace_root(arguments.framework_dir, arguments.workspace_root)
        raw = stdin.read()
        payload = json.loads(raw.decode("utf-8-sig")) if raw.strip() else {}
        if not isinstance(payload, Mapping):
            raise ValueError("the hook payload is not a JSON object")
        adapter, binding = build(arguments.framework_dir, arguments.workspace_dir, deadline)
        hook = _hook(arguments.event, payload, binding)
        # The session's workspace was fixed when its record was created (adapter spec,
        # Session identity): a later firing never re-resolves it.
        recorded = adapter.recorded_workspace(hook)
        if recorded and recorded != adapter.workspace_dir:
            adapter, binding = build(arguments.framework_dir, recorded, deadline)
            hook = _hook(arguments.event, payload, binding)
        decision = adapter.handle(hook)
    except Exception as failure:  # noqa: BLE001 - the entry must never crash open
        print(
            f"[{ADAPTER_ID}] {arguments.event}: the hook failed — failing closed: "
            f"{type(failure).__name__}: {failure}",
            file=stderr,
        )
        stderr.flush()
        return failure_exit(arguments.event)
    output = renderer.render(decision, arguments.event)
    if output:
        stdout.write(output + "\n")
        stdout.flush()
    return 0


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="adapter.py")
    parser.add_argument("command", choices=("hook",))
    parser.add_argument("--event", required=True, choices=EVENTS)
    parser.add_argument("--framework-dir", default=None)
    parser.add_argument("--workspace-dir", default=None)
    parser.add_argument("--workspace-root", default=None)
    return parser.parse_args(list(argv))


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: Sequence[str] | None = None) -> int:
    _force_utf8_stdio()
    arguments = list(argv) if argv is not None else sys.argv[1:]
    try:
        return run_hook(
            argv=arguments, stdin=sys.stdin.buffer, stdout=sys.stdout, stderr=sys.stderr
        )
    except SystemExit as exit_:
        # argparse refuses a malformed command line with exit 2: keep that only where the
        # runtime can veto.
        return bootstrap_exit(arguments) if exit_.code else 0


if __name__ == "__main__":
    raise SystemExit(main())
