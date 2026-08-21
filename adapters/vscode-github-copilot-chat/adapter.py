#!/usr/bin/env python3
"""The `vscode-github-copilot-chat` hook entry: classify, resolve, invoke, render.

`dispatch.sh <event> vscode-github-copilot-chat [<agent>]` execs this module with the
host event JSON on stdin (seam 1) and expects the host decision JSON on stdout (seam 4).
Everything host-aware for this host lives in this directory; the only dependency into the
harness core is its command API (adapter spec I15) — never `services`, `stores`, or
`config`, and never the session log.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from command_runner import CommandRunner, SubprocessCommandRunner
from event_class import EventClass
from hook_binding import HookBinding, load_hook_binding
from hook_classifier import HookClassifier
from hook_event import HookDecision, HookEvent
from hook_renderer import HookRenderer
from session_identity import (
    derive_step_session_id,
    derive_turn_session_id,
    sanitize_identifier,
)
from session_tracker import SessionTracker

_SUBAGENT_STOP = "SubagentStop"
_STARTED_STATUS = "started"
_NOT_APPLICABLE_STATUS = "not-applicable"
_AGENT_INVOCABLE_FUNCTIONS = frozenset({"resolve-step", "resolve-step-model"})
_ATTRIBUTION_FLAGS = ("--session-id", "--parent-session-id")
_HARNESS_COMMAND = re.compile(
    r"(?:^|[\s;&|(])(?:\S*/)?harness\.py\s+([a-z][a-z0-9-]*)"
)
_TOKEN_TRIM = "'\"();|&<>"


class Adapter:
    """Orchestrate one host firing across the harness command API.

    Spec (adapter, I14/I15): sequencing (registration first), session resolution,
    per-path fan-out, and abort-on-failure are this adapter's own behavior; framework-
    agent gating (C7), step correlation, and session-closure enforcement (C8) are decided
    INSIDE the invoked commands, never here — this class only relays their outcomes.
    """

    def __init__(
        self,
        binding: HookBinding,
        classifier: HookClassifier,
        tracker: SessionTracker,
        renderer: HookRenderer,
        command_runner: CommandRunner,
        workspace_dir: Path,
    ) -> None:
        """Create the adapter over its binding, tracker, renderer, and command API."""
        self._binding = binding
        self._classifier = classifier
        self._tracker = tracker
        self._renderer = renderer
        self._command_runner = command_runner
        self._workspace_dir = workspace_dir
        self._handlers: Mapping[EventClass, Callable[[HookEvent], HookDecision]] = {
            EventClass.SESSION_STARTED: self._handle_session_started,
            EventClass.STEP_STARTED: self._handle_step_started,
            EventClass.STEP_STARTING: self._handle_step_starting,
            EventClass.WRITE_STARTING: self._handle_write_starting,
            EventClass.MEDIATED_ATTRIBUTION: self._handle_mediated_attribution,
            EventClass.WRITE_ENDED: self._handle_write_ended,
            EventClass.STEP_ENDED: self._handle_step_ended,
            EventClass.SESSION_ENDED: self._handle_session_ended,
        }

    def handle_hook_event(self, event: HookEvent) -> HookDecision:
        """Handle one host firing, answering the decision the host will honor."""
        handler = self._handlers.get(self._classifier.classify_event(event))
        if handler is None:
            return self._renderer.render_pass_through()
        return handler(event)

    def _handle_session_started(self, event: HookEvent) -> HookDecision:
        """H0 — open the orchestrator turn session and inject its workflow context."""
        host_id = sanitize_identifier(event.raw_host_session_id)
        session_id = derive_turn_session_id(event.raw_host_session_id, event.timestamp)
        try:
            registration = self._command_runner.run_function(
                "start-session",
                {
                    "agent": event.scoping_agent,
                    "sessionId": session_id,
                    "parentSessionId": None,
                },
            )
            if _read_status(registration) != _STARTED_STATUS:
                self._tracker.clear_current(host_id)
                return self._renderer.render_pass_through()
            self._tracker.reset_current(host_id, session_id)
            inquiry = {"sessionId": session_id, "parentSessionId": None}
            reports = (
                self._command_runner.run_function("resolve-workflow-instructions", inquiry),
                self._command_runner.run_function("resolve-workflow-skills", inquiry),
            )
            return self._renderer.render_context_injection(
                EventClass.SESSION_STARTED, reports
            )
        except Exception as failure:
            return self._renderer.render_system_message(str(failure))

    def _handle_step_started(self, event: HookEvent) -> HookDecision:
        """H1 — register the step session under its dispatcher and inject its context."""
        host_id = sanitize_identifier(event.raw_host_session_id)
        parent_session_id = self._tracker.resolve_current(host_id)
        agent_id = self._binding.probe_payload_value(
            event.payload, self._binding.host_step_session_keys
        )
        actor = self._binding.probe_payload_value(
            event.payload, self._binding.host_step_actor_keys
        )
        if parent_session_id is None or agent_id is None or actor is None:
            return self._renderer.render_pass_through()
        session_id = derive_step_session_id(str(agent_id))
        try:
            registration = self._command_runner.run_function(
                "start-session",
                {
                    "agent": str(actor),
                    "sessionId": session_id,
                    "parentSessionId": parent_session_id,
                },
            )
            if _read_status(registration) != _STARTED_STATUS:
                return self._renderer.render_pass_through()
            self._tracker.push_current(host_id, session_id)
            inquiry = {"sessionId": session_id, "parentSessionId": parent_session_id}
            reports = (
                self._command_runner.run_function("resolve-step-instructions", inquiry),
                self._command_runner.run_function("resolve-step-skills", inquiry),
            )
            return self._renderer.render_context_injection(
                EventClass.STEP_STARTED, reports
            )
        except Exception as failure:
            return self._renderer.render_system_message(str(failure))

    def _handle_step_starting(self, event: HookEvent) -> HookDecision:
        """H2 — enforce the step's preconditions before the dispatch executes."""
        session_id = self._tracker.resolve_current(
            sanitize_identifier(event.raw_host_session_id)
        )
        if session_id is None:
            return self._renderer.render_pass_through()
        try:
            report = self._command_runner.run_function(
                "check-step-preconditions",
                {"sessionId": session_id, "parentSessionId": None},
            )
        except Exception as failure:
            return self._renderer.render_denial(
                f"check-step-preconditions unavailable: {failure}"
            )
        if _read_status(report) == _NOT_APPLICABLE_STATUS:
            return self._renderer.render_pass_through()
        return self._renderer.render_permission_decision((report,))

    def _handle_write_starting(self, event: HookEvent) -> HookDecision:
        """H3 — authorize every artifact path of the call before the write lands."""
        host_id = sanitize_identifier(event.raw_host_session_id)
        session_id = self._tracker.resolve_current(host_id)
        artifact_paths = self._binding.extract_artifact_paths(event.tool_input)
        action = self._binding.resolve_write_action(str(event.tool_name))
        if session_id is None or not artifact_paths or action is None:
            return self._renderer.render_pass_through()
        inquiry = {
            "sessionId": session_id,
            "parentSessionId": self._tracker.resolve_parent(host_id),
            "action": action,
        }
        try:
            reports = tuple(
                self._command_runner.run_function(
                    "check-step-authorization",
                    {**inquiry, "artifactPath": self._relativize_path(path)},
                )
                for path in artifact_paths
            )
        except Exception as failure:
            return self._renderer.render_denial(
                f"check-step-authorization unavailable: {failure}"
            )
        return self._renderer.render_permission_decision(reports)

    def _handle_mediated_attribution(self, event: HookEvent) -> HookDecision:
        """H4 — stamp the host-observed session onto a model-authored harness command."""
        command_key = self._find_command_key(event.tool_input)
        if command_key is None:
            return self._renderer.render_pass_through()
        command = str(event.tool_input[command_key])
        function = (
            _match_harness_function(command)
            if self._binding.is_mediated_command_tool(event.tool_name)
            else None
        )
        if function is None:
            return self._decide_guarded_shell(event, command)
        if function not in _AGENT_INVOCABLE_FUNCTIONS:
            return self._renderer.render_denial(
                f"harness function '{function}' is not agent-invocable: only "
                "resolve-step and resolve-step-model are"
            )
        carried_flag = next(
            (flag for flag in _ATTRIBUTION_FLAGS if flag in command), None
        )
        if carried_flag is not None:
            return self._renderer.render_denial(
                f"model-authored session attribution ('{carried_flag}') is never accepted"
            )
        session_id = self._tracker.resolve_current(
            sanitize_identifier(event.raw_host_session_id)
        )
        if session_id is None:
            return self._renderer.render_denial(
                "no registered agent session to attribute this harness command to"
            )
        return self._renderer.render_stamped_input(
            event.tool_input, command_key, session_id
        )

    def _handle_write_ended(self, event: HookEvent) -> HookDecision:
        """H5 — the commit gate: validate the landed write set, or report its revert."""
        host_id = sanitize_identifier(event.raw_host_session_id)
        session_id = self._tracker.resolve_current(host_id)
        artifact_paths = self._binding.extract_artifact_paths(event.tool_input)
        if session_id is None or not artifact_paths:
            return self._renderer.render_pass_through()
        try:
            report = self._command_runner.run_function(
                "check-step-artifact",
                {
                    "sessionId": session_id,
                    "parentSessionId": self._tracker.resolve_parent(host_id),
                    "artifactPaths": [
                        self._relativize_path(path) for path in artifact_paths
                    ],
                },
            )
        except Exception as failure:
            return self._renderer.render_block(
                f"check-step-artifact unavailable: {failure}",
                "The write could not be validated. Retry once the harness answers.",
            )
        if _read_status(report) == _NOT_APPLICABLE_STATUS:
            return self._renderer.render_pass_through()
        return self._renderer.render_write_outcome(report)

    def _handle_step_ended(self, event: HookEvent) -> HookDecision:
        """H6 — evaluate the returned step in the DISPATCHING session (the stack base)."""
        session_id = self._tracker.resolve_base(
            sanitize_identifier(event.raw_host_session_id)
        )
        if session_id is None:
            return self._renderer.render_pass_through()
        try:
            report = self._command_runner.run_function(
                "check-step-postconditions",
                {"sessionId": session_id, "parentSessionId": None},
            )
        except Exception as failure:
            return self._renderer.render_block(
                f"check-step-postconditions unavailable: {failure}",
                "The step outcome could not be evaluated. Re-resolve (resolve-step).",
            )
        if _read_status(report) == _NOT_APPLICABLE_STATUS:
            return self._renderer.render_pass_through()
        return self._renderer.render_step_outcome(report)

    def _handle_session_ended(self, event: HookEvent) -> HookDecision:
        """H7 — close the ending session, then pop (`SubagentStop`) or clear (`Stop`)."""
        host_id = sanitize_identifier(event.raw_host_session_id)
        if event.hook_event_name == _SUBAGENT_STOP:
            agent_id = self._binding.probe_payload_value(
                event.payload, self._binding.host_step_session_keys
            )
            if agent_id is None:
                return self._renderer.render_pass_through()
            ending_session_id = derive_step_session_id(str(agent_id))
            if self._tracker.resolve_current(host_id) != ending_session_id:
                return self._renderer.render_pass_through()
            self._close_session(ending_session_id)
            self._tracker.pop_current(host_id)
            return self._renderer.render_pass_through()
        ending_session_id = self._tracker.resolve_current(host_id)
        if ending_session_id is None:
            return self._renderer.render_pass_through()
        self._close_session(ending_session_id)
        self._tracker.clear_current(host_id)
        return self._renderer.render_pass_through()

    def _close_session(self, session_id: str) -> None:
        try:
            self._command_runner.run_function("end-session", {"sessionId": session_id})
        except Exception as failure:
            _report_diagnostic(f"end-session failed for '{session_id}': {failure}")

    def _decide_guarded_shell(self, event: HookEvent, command: str) -> HookDecision:
        """Deny a non-harness shell command that textually names a governed path (I9)."""
        if not self._binding.is_guarded_shell_tool(event.tool_name):
            return self._renderer.render_pass_through()
        offending_path = self._find_guarded_path(command)
        if offending_path is None:
            return self._renderer.render_pass_through()
        return self._renderer.render_denial(
            f"guarded shell command references the governed workspace path "
            f"'{offending_path}': workspace writes go through the harness's own write "
            "boundary"
        )

    def _find_guarded_path(self, command: str) -> str | None:
        for token in command.split():
            candidate = token.strip(_TOKEN_TRIM)
            if any(marker in candidate for marker in self._binding.guarded_path_markers):
                return candidate
        return None

    def _find_command_key(self, tool_input: Mapping[str, Any]) -> str | None:
        return next(
            (
                key
                for key in self._binding.mediated_command_keys
                if isinstance(tool_input.get(key), str)
            ),
            None,
        )

    def _relativize_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self._workspace_dir))
        except ValueError:
            return str(path)


def build_default_adapter() -> Adapter:
    """Build the adapter the host entry uses, from this adapter's own sources."""
    adapter_dir = Path(__file__).resolve().parent
    framework_dir = Path(os.environ.get("FRAMEWORK_DIR", adapter_dir.parents[1]))
    workspace_dir = _resolve_layout_dir(framework_dir, "FRAMEWORK_WORKSPACE_DIR")
    binding = load_hook_binding(
        adapter_dir,
        guarded_path_markers=(f"{workspace_dir.name}/", "logs/"),
    )
    return Adapter(
        binding=binding,
        classifier=HookClassifier(binding),
        tracker=SessionTracker(adapter_dir / ".session-tracker.json"),
        renderer=HookRenderer(
            instructions_dir=_resolve_layout_dir(framework_dir, "FRAMEWORK_INSTRUCTIONS_DIR"),
            skills_dir=_resolve_layout_dir(framework_dir, "FRAMEWORK_SKILLS_DIR"),
        ),
        command_runner=SubprocessCommandRunner(
            harness_entrypoint=framework_dir / "harness.py"
        ),
        workspace_dir=workspace_dir,
    )


def run_hook_entry(
    argv: Sequence[str],
    stdin_text: str,
    write_stdout: Callable[[str], Any],
    build_adapter: Callable[[], Adapter],
) -> int:
    """Run one hook invocation end to end: stdin payload in, host decision out."""
    arguments = _parse_arguments(argv)
    event = HookEvent.build_from_payload(
        json.loads(stdin_text), scoping_agent=arguments.agent
    )
    decision = build_adapter().handle_hook_event(event)
    if decision.stdout:
        write_stdout(decision.stdout)
    return decision.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    """Run the host entry `dispatch.sh` execs."""
    return run_hook_entry(
        argv=list(argv) if argv is not None else sys.argv[1:],
        stdin_text=sys.stdin.read(),
        write_stdout=sys.stdout.write,
        build_adapter=build_default_adapter,
    )


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="adapter.py")
    parser.add_argument("command", choices=("hook",))
    parser.add_argument("--event", required=True)
    parser.add_argument("--agent", default=None)
    return parser.parse_args(list(argv))


def _resolve_layout_dir(framework_dir: Path, variable: str) -> Path:
    declared = os.environ.get(variable)
    return framework_dir / declared if declared else framework_dir


def _read_status(report: Mapping[str, Any]) -> str:
    return str((report.get("outcome") or {}).get("status", ""))


def _match_harness_function(command: str) -> str | None:
    """Return the harness function a shell command invokes, or None (H4, rule 1)."""
    match = _HARNESS_COMMAND.search(command)
    return match.group(1) if match else None


def _report_diagnostic(message: str) -> None:
    """Write an operational diagnostic — H7 has no host-visible effect either way."""
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
