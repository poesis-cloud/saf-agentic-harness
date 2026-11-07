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

from command_runner import CommandRunner, HarnessCommandError, SubprocessCommandRunner
from event_class import EventClass
from hook_binding import HookBinding, load_hook_binding
from hook_classifier import HookClassifier
from hook_event import HookDecision, HookEvent
from hook_renderer import HookRenderer
from session_identity import (
    derive_worker_session_id,
    derive_turn_session_id,
    sanitize_identifier,
)
from session_tracker import UNREGISTERED_PREFIX, SessionTracker, is_unregistered

_SUBAGENT_STOP = "SubagentStop"
_HARNESS_ENTRYPOINT_NAME = "harness.py"
_SESSION_RECORD_NAME = "session-tracker.json"
_STATE_HOME_VARIABLE = "XDG_STATE_HOME"
_STATE_NAMESPACE = "saf-agentic-harness"
_STARTED_STATUS = "started"
_NOT_APPLICABLE_STATUS = "not-applicable"
_UNREGISTERED_REASON = (
    "this session is not registered with the harness (its start-session failed): its "
    "writes, dispatches and harness requests are denied until a registration succeeds "
    "(fail closed)"
)
_AGENT_INVOCABLE_FUNCTIONS = frozenset({"resolve-step", "resolve-step-model"})
_ATTRIBUTION_FLAGS = ("--session-id", "--parent-session-id")
_HARNESS_COMMAND = re.compile(
    r"(?:^|[\s;&|(])(?:\S*[/\\])?harness\.py['\"]?\s+([a-z][a-z0-9-]*)"
)
_TOKEN_TRIM = "'\"();|&<>"
# ONE plain request: `[&] [<python|python3|py|interpreter path> [-3[.x]]] <…harness.py> <fn>
# [--flag value]...` — no chaining, piping, redirection, substitution or subexpression.
_PLAIN = r"""[^\s'";|&`$()<>{}]"""
_QUOTED = r"""(?:'[^'`$;|&<>]*'|"[^"`$;|&<>]*")"""
_HARNESS_PATH = (
    r"""(?:'(?:[^'`$;|&<>]*[/\\])?harness\.py'"""
    r"""|"(?:[^"`$;|&<>]*[/\\])?harness\.py\""""
    rf"""|(?:{_PLAIN}*[/\\])?harness\.py)"""
)
_STRICT_HARNESS_REQUEST = re.compile(
    rf"""^\s*(?:&\s*)?(?:(?P<interpreter>{_QUOTED}|{_PLAIN}+)(?:\s+-[0-9][0-9.]*)?\s+)?"""
    rf"""(?P<harness>{_HARNESS_PATH})\s+(?P<function>[a-z][a-z0-9-]*)"""
    rf"""(?P<flags>(?:\s+(?:{_QUOTED}|{_PLAIN}+))*)\s*$"""
)
_FLAG_TOKEN = re.compile(rf"{_QUOTED}|{_PLAIN}+")
_INTERPRETER_NAME = re.compile(r"^(?:python(?:[0-9]+(?:\.[0-9]+)*)?|py)(?:\.exe)?$", re.I)
# The deny reason is the tool result the host hands the agent (`Tool execution denied:
# <reason>`), so it carries the harness's answer; these prefixes tell a result from a
# refusal of the request itself.
HARNESS_RESULT = "SAFe agentic harness RESULT"
HARNESS_REJECTED = "SAFe agentic harness REJECTED"
_RESULT_DIRECTIVE = (
    "this is the successful answer to your harness command, not an error. The harness hook "
    "ran '{function}' for your session itself, so your terminal did not run the line (the "
    "host shows that as a denial). Act on this report; do not re-issue the command to get "
    "it again."
)
_RESULT_CONTEXT = (
    "The 'Tool execution denied' result above is the harness's answer, not a failure: the "
    "harness hook ran '{function}' for your session and returned its report. Act on it; do "
    "not re-issue the command."
)
_FRAMEWORK_DIR_VARIABLE = "FRAMEWORK_DIR"
_LAYOUT_ENV_FILENAME = ".env"
_EXIT_FAIL_CLOSED = 2


class AdapterBootstrapError(RuntimeError):
    """The hook cannot bootstrap; the host must see a blocking failure, never fail open.

    Spec (adapter, Bootstrap failure): a missing framework anchor or an unreadable layout
    is refused rather than guessed — a guessed root would govern the wrong tree.
    """


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
        except Exception as failure:
            self._tracker.reset_unregistered(host_id, session_id)
            return self._renderer.render_system_message(str(failure))
        status = _read_status(registration)
        if status == _NOT_APPLICABLE_STATUS:
            self._tracker.clear_current(host_id)
            return self._renderer.render_pass_through()
        if status != _STARTED_STATUS:
            # Only `not-applicable` means ungoverned: a failed registration fails closed.
            self._tracker.reset_unregistered(host_id, session_id)
            return self._renderer.render_system_message(
                f"the harness could not register this session: {_describe(registration)}"
            )
        try:
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
        """H1 — register the worker session under its dispatcher and inject its context."""
        host_id = sanitize_identifier(event.raw_host_session_id)
        parent_session_id = self._tracker.resolve_current(host_id)
        agent_id = self._binding.probe_payload_value(
            event.payload, self._binding.host_worker_session_keys
        )
        actor = self._binding.probe_payload_value(
            event.payload, self._binding.host_step_actor_keys
        )
        if parent_session_id is None:
            return self._renderer.render_pass_through()
        if agent_id is None:
            return self._renderer.render_pass_through()
        session_id = derive_worker_session_id(str(agent_id))
        if actor is None:
            # Never registered as the step's worker, yet attributable: its return must not
            # conclude the step (H6).
            self._ignore_return(host_id, session_id)
            return self._renderer.render_pass_through()
        if is_unregistered(parent_session_id):
            self._tracker.push_unregistered(host_id, session_id)
            self._ignore_return(host_id, session_id)
            return self._renderer.render_pass_through()
        try:
            registration = self._command_runner.run_function(
                "start-session",
                {
                    "agent": str(actor),
                    "sessionId": session_id,
                    "parentSessionId": parent_session_id,
                },
            )
        except Exception as failure:
            self._tracker.push_unregistered(host_id, session_id)
            self._ignore_return(host_id, session_id)
            return self._renderer.render_system_message(str(failure))
        status = _read_status(registration)
        if status == _NOT_APPLICABLE_STATUS:
            self._ignore_return(host_id, session_id)
            return self._renderer.render_pass_through()
        if status != _STARTED_STATUS:
            # `worker-session-bound` and every other error: the step is pushed UNREGISTERED,
            # so its writes and dispatches are denied rather than attributed or passed, and
            # its return does not conclude the step its bound worker session still runs.
            self._tracker.push_unregistered(host_id, session_id)
            self._ignore_return(host_id, session_id)
            return self._renderer.render_system_message(
                f"the harness could not register this worker session: {_describe(registration)}"
            )
        try:
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

    def _ignore_return(self, host_id: str, session_id: str) -> None:
        """Mark this sub-agent's dispatch return as not concluding the step (H6).

        Adapter spec H6: a dispatch return whose sub-agent's worker-session registration
        was refused, or that was never registered as the step's worker, does not trigger
        `check-step-postconditions`; the step stays in flight for its bound worker session.
        """
        self._tracker.mark_ignored_return(host_id, session_id)

    def _handle_step_starting(self, event: HookEvent) -> HookDecision:
        """H2 — enforce the step's preconditions before the dispatch executes."""
        session_id = self._tracker.resolve_current(
            sanitize_identifier(event.raw_host_session_id)
        )
        if session_id is None:
            return self._renderer.render_pass_through()
        if is_unregistered(session_id):
            return self._renderer.render_denial(_UNREGISTERED_REASON)
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
        tool_name = str(event.tool_name)
        if session_id is None or self._binding.resolve_write_action(tool_name) is None:
            return self._renderer.render_pass_through()
        if is_unregistered(session_id):
            return self._renderer.render_denial(_UNREGISTERED_REASON)
        path_actions = self._binding.resolve_path_actions(tool_name, event.tool_input)
        if not path_actions:
            return self._renderer.render_denial(_unextractable_write_reason(tool_name))
        inquiry = {
            "sessionId": session_id,
            "parentSessionId": self._tracker.resolve_parent(host_id),
        }
        try:
            reports = tuple(
                self._command_runner.run_function(
                    "check-step-authorization",
                    {
                        **inquiry,
                        "action": action,
                        "artifactPath": self._relativize_path(path),
                    },
                )
                for path, action in path_actions
            )
        except Exception as failure:
            return self._renderer.render_denial(
                f"check-step-authorization unavailable: {failure}"
            )
        return self._renderer.render_permission_decision(reports)

    def _handle_mediated_attribution(self, event: HookEvent) -> HookDecision:
        """H4 — answer an agent's harness request by running it in the hook.

        The agent's command is a REQUEST, never a command to run or edit: the hook runs
        functions 3–4 itself for the session the host-observed `session_id` resolves to,
        and returns the report as the deny reason (adapter spec H4).
        """
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
            return self._reject(
                f"harness function '{function}' is not agent-invocable: only "
                "resolve-step and resolve-step-model are"
            )
        carried_flag = next(
            (flag for flag in _ATTRIBUTION_FLAGS if flag in command), None
        )
        if carried_flag is not None:
            return self._reject(
                f"model-authored session attribution ('{carried_flag}') is never "
                "accepted: the harness hook attributes the request to your session itself"
            )
        rejected, inquiry = _parse_harness_request(command, function)
        if rejected is not None:
            return self._reject(rejected)
        host_id = sanitize_identifier(event.raw_host_session_id)
        session_id = self._tracker.resolve_current(host_id)
        if session_id is None:
            return self._reject(
                "no registered agent session to attribute this harness command to"
            )
        if is_unregistered(session_id):
            return self._reject(_UNREGISTERED_REASON)
        return self._answer_harness_request(
            function,
            {
                **inquiry,
                "sessionId": session_id,
                "parentSessionId": self._tracker.resolve_parent(host_id),
            },
        )

    def _answer_harness_request(
        self, function: str, inquiry: Mapping[str, Any]
    ) -> HookDecision:
        try:
            report = self._command_runner.run_function(function, inquiry)
        except HarnessCommandError as failure:
            _report_diagnostic(f"{function} failed: {failure}")
            return self._reject(
                failure.refusal
                or "the harness could not answer (see the hook diagnostics)"
            )
        except Exception as failure:  # noqa: BLE001 - every failure must be contained
            _report_diagnostic(f"{function} failed: {failure}")
            return self._reject("the harness could not answer (see the hook diagnostics)")
        rendered = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        return self._renderer.render_harness_answer(
            f"{HARNESS_RESULT} ({function}): "
            f"{_RESULT_DIRECTIVE.format(function=function)}\n\n{rendered}",
            _RESULT_CONTEXT.format(function=function),
        )

    def _reject(self, reason: str) -> HookDecision:
        return self._renderer.render_denial(f"{HARNESS_REJECTED}: {reason}")

    def _handle_write_ended(self, event: HookEvent) -> HookDecision:
        """H5 — the commit gate: validate the landed write set, or report its revert."""
        host_id = sanitize_identifier(event.raw_host_session_id)
        session_id = self._tracker.resolve_current(host_id)
        if session_id is None or is_unregistered(session_id):
            return self._renderer.render_pass_through()
        artifact_paths = self._binding.extract_artifact_paths(event.tool_input)
        if not artifact_paths:
            return self._renderer.render_block(
                _unextractable_write_reason(str(event.tool_name)),
                "The write went unvalidated. Redo it with a tool call that names its "
                "artifact paths.",
            )
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
        return self._renderer.render_write_outcome(report)

    def _handle_step_ended(self, event: HookEvent) -> HookDecision:
        """H6 — evaluate the returned step in the DISPATCHING session (the stack base)."""
        host_id = sanitize_identifier(event.raw_host_session_id)
        session_id = self._tracker.resolve_base(host_id)
        if session_id is None or is_unregistered(session_id):
            return self._renderer.render_pass_through()
        if self._tracker.consume_ignored_return(host_id):
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
                event.payload, self._binding.host_worker_session_keys
            )
            if agent_id is None:
                return self._renderer.render_pass_through()
            ending_session_id = derive_worker_session_id(str(agent_id))
            # H6: a sub-agent recorded as ignored at H1 has now stopped, so the next
            # dispatch return is its own (I13(e)) and is not evaluated.
            self._tracker.arm_ignored_return(host_id, ending_session_id)
            current = self._tracker.resolve_current(host_id)
            if current == f"{UNREGISTERED_PREFIX}{ending_session_id}":
                self._tracker.pop_current(host_id)
                return self._renderer.render_pass_through()
            if current != ending_session_id:
                return self._renderer.render_pass_through()
            self._close_session(ending_session_id)
            self._tracker.pop_current(host_id)
            return self._renderer.render_pass_through()
        ending_session_id = self._tracker.resolve_current(host_id)
        if ending_session_id is None:
            return self._renderer.render_pass_through()
        if not is_unregistered(ending_session_id):
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
            return path.relative_to(self._workspace_dir).as_posix()
        except ValueError:
            return path.as_posix()


def build_default_adapter() -> Adapter:
    """Build the adapter the host entry uses, from this adapter's own sources."""
    adapter_dir = Path(__file__).resolve().parent
    harness_dir = adapter_dir.parents[1]
    framework_dir = _resolve_framework_dir()
    layout = _load_layout_file(framework_dir / _LAYOUT_ENV_FILENAME)
    workspace_dir = _resolve_layout_dir(framework_dir, layout, "FRAMEWORK_WORKSPACE_DIR")
    binding = load_hook_binding(
        adapter_dir,
        guarded_path_markers=(f"{workspace_dir.name}/", "logs/"),
        framework_dir=framework_dir,
    )
    return Adapter(
        binding=binding,
        classifier=HookClassifier(binding),
        tracker=SessionTracker(_resolve_state_dir(adapter_dir) / _SESSION_RECORD_NAME),
        renderer=HookRenderer(
            instructions_dir=_resolve_layout_dir(
                framework_dir, layout, "FRAMEWORK_INSTRUCTIONS_DIR"
            ),
            skills_dir=_resolve_layout_dir(framework_dir, layout, "FRAMEWORK_SKILLS_DIR"),
        ),
        command_runner=SubprocessCommandRunner(
            harness_entrypoint=harness_dir / _HARNESS_ENTRYPOINT_NAME
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
    """Run the host entry `dispatch.sh` runs.

    Spec (adapter, Bootstrap failure): any failure escaping the hook handling — a refused
    bootstrap, a refused binding, an unexpected crash — answers exit 2 with a stderr
    diagnostic (the host's blocking channel), never an exit the host fails open on.
    """
    try:
        _force_utf8_stdio()
        return run_hook_entry(
            argv=list(argv) if argv is not None else sys.argv[1:],
            stdin_text=_read_stdin_text(),
            write_stdout=sys.stdout.write,
            build_adapter=build_default_adapter,
        )
    except Exception as failure:
        _report_diagnostic(
            f"adapter.py: the hook failed — failing closed: "
            f"{type(failure).__name__}: {failure}"
        )
        return _EXIT_FAIL_CLOSED


def _force_utf8_stdio() -> None:
    """Answer UTF-8 whatever the console codepage, so the host reads one encoding."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def _read_stdin_text() -> str:
    """Read the host payload as UTF-8 (BOM tolerated), not the console codepage."""
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return sys.stdin.read()
    return buffer.read().decode("utf-8-sig")


def _parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="adapter.py")
    parser.add_argument("command", choices=("hook",))
    parser.add_argument("--event", required=True)
    parser.add_argument("--agent", default=None)
    return parser.parse_args(list(argv))


def _resolve_framework_dir() -> Path:
    declared = os.environ.get(_FRAMEWORK_DIR_VARIABLE)
    if not declared:
        raise AdapterBootstrapError(
            f"{_FRAMEWORK_DIR_VARIABLE} is not set — every rendered hook entry records it "
            "in its env; re-run `make install-hooks FRAMEWORK_DIR=<framework root>`"
        )
    framework_dir = Path(declared)
    if not framework_dir.is_dir():
        raise AdapterBootstrapError(
            f"{_FRAMEWORK_DIR_VARIABLE} names no directory: {declared}"
        )
    return framework_dir


def _load_layout_file(path: Path) -> Mapping[str, str]:
    """Read the framework's `.env` KEY=VALUE layout file, as the core config loader does."""
    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as failure:
        raise AdapterBootstrapError(f"{path} cannot be read: {failure}") from failure
    declared: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator == "" or key.strip() == "":
            raise AdapterBootstrapError(
                f"{path} line {line_number} is malformed: expected KEY=VALUE"
            )
        declared[key.strip()] = value.strip()
    return declared


def _resolve_layout_dir(
    framework_dir: Path, layout: Mapping[str, str], variable: str
) -> Path:
    """Resolve one layout directory; the process environment wins over `.env`."""
    declared = os.environ.get(variable, layout.get(variable))
    return framework_dir / declared if declared else framework_dir


def _resolve_state_dir(adapter_dir: Path) -> Path:
    """Resolve this adapter's per-user runtime state directory, outside every repo.

    The tracker's record is runtime state, not source and not governed data: inside the
    harness checkout it dirties version control every turn and breaks a read-only or
    shared deployment, and inside the workspace it would breach the Git plane's
    artifacts-plus-logs ring fence.
    """
    declared = os.environ.get(_STATE_HOME_VARIABLE)
    state_home = Path(declared) if declared else Path.home() / ".local" / "state"
    return state_home / _STATE_NAMESPACE / adapter_dir.name


def _unextractable_write_reason(tool_name: str) -> str:
    return (
        f"write tool '{tool_name}' names no artifact path the adapter can extract: "
        "an unauthorizable write fails closed"
    )


def _describe(report: Mapping[str, Any]) -> str:
    error = (report.get("outcome") or {}).get("error")
    if isinstance(error, Mapping):
        return f"{_read_status(report)}: {error.get('code')} — {error.get('message')}"
    return _read_status(report) or "no outcome"


def _read_status(report: Mapping[str, Any]) -> str:
    return str((report.get("outcome") or {}).get("status", ""))


def _match_harness_function(command: str) -> str | None:
    """Return the harness function a shell command invokes, or None (H4, rule 1)."""
    match = _HARNESS_COMMAND.search(command)
    return match.group(1) if match else None


def _parse_harness_request(
    command: str, function: str
) -> tuple[str | None, dict[str, str]]:
    """Read ONE plain harness request into its inquiry, or answer why it is rejected."""
    example = f"python harness.py {function}" + (
        " --workflow-slug <workflow-slug>" if function == "resolve-step" else ""
    )
    match = _STRICT_HARNESS_REQUEST.match(command)
    if match is None or match.group("function") != function:
        return (
            "a harness command must be one plain invocation, never chained, piped or "
            f"redirected: {example}"
        ), {}
    interpreter = match.group("interpreter")
    if interpreter is not None and not _INTERPRETER_NAME.match(
        re.split(r"[/\\]", _unquote(interpreter))[-1]
    ):
        return f"'{_unquote(interpreter)}' is not a Python interpreter: {example}", {}
    tokens = [_unquote(token) for token in _FLAG_TOKEN.findall(match.group("flags"))]
    inquiry: dict[str, str] = {}
    for index in range(0, len(tokens), 2):
        flag = tokens[index]
        value = tokens[index + 1] if index + 1 < len(tokens) else None
        if not flag.startswith("--") or len(flag) < 3:
            return f"'{flag}' is not a '--flag value' argument: {example}", {}
        if value is None or value.startswith("--"):
            return f"flag '{flag}' needs a value", {}
        key = _camel(flag[2:])
        if key in inquiry:
            return f"flag '{flag}' is given more than once", {}
        inquiry[key] = value
    return None, inquiry


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


def _camel(kebab: str) -> str:
    head, *rest = kebab.split("-")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _report_diagnostic(message: str) -> None:
    """Write an operational diagnostic — H7 has no host-visible effect either way."""
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
