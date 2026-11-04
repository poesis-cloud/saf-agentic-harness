"""The `github-copilot-cli` adapter: one host firing in, one host decision out.

Everything host-aware for the Copilot CLI runtime (and the GitHub Copilot app that embeds
it) lives in this package; the only edge into the harness core is its command API
(adapter spec I15). Framework-agent gating, step correlation and session closure are
decided INSIDE the invoked commands — this class sequences them and relays outcomes.

Boundary map (see `def/adapter/github-copilot-cli/spec.md`):

    H0  userPromptSubmitted / userPromptTransformed (agent-scoped)  start-session (turn)
    H1  subagentStart                                               start-session (step)
    H2  preToolUse · dispatch tool (`task`)                         check-step-preconditions
    H3  preToolUse · write tool                                     check-step-authorization
    H4  preToolUse · shell tool requesting `harness.py`             resolve-step / resolve-step-model
                                                                    (run BY the hook; never by the shell)
    H5  postToolUse · write tool                                    check-step-artifact
    H6  postToolUse / postToolUseFailure · dispatch tool            check-step-postconditions
    H7  agentStop / subagentStop / sessionEnd                       end-session
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from . import renderer
from .binding import TargetExtractionError, ToolBinding
from .invocation import HarnessInvocation, unquote
from .paths import host_basename, relativize
from .payload import HookInput, MalformedPayloadError, ToolCall, sanitize_identifier
from .renderer import ContextRenderer, read_status
from .runner import CommandRunner, HarnessCommandError
from .tracker import STEP_ORIGIN, TURN_ORIGIN, SessionRecord, SessionTracker

ADAPTER_ID = "github-copilot-cli"

SESSION_START = "sessionStart"
USER_PROMPT_SUBMITTED = "userPromptSubmitted"
USER_PROMPT_TRANSFORMED = "userPromptTransformed"
PRE_TOOL_USE = "preToolUse"
POST_TOOL_USE = "postToolUse"
POST_TOOL_USE_FAILURE = "postToolUseFailure"
SUBAGENT_START = "subagentStart"
SUBAGENT_STOP = "subagentStop"
AGENT_STOP = "agentStop"
SESSION_END = "sessionEnd"
ERROR_OCCURRED = "errorOccurred"
EVENTS = (
    SESSION_START,
    USER_PROMPT_SUBMITTED,
    USER_PROMPT_TRANSFORMED,
    PRE_TOOL_USE,
    POST_TOOL_USE,
    POST_TOOL_USE_FAILURE,
    SUBAGENT_START,
    SUBAGENT_STOP,
    AGENT_STOP,
    SESSION_END,
    ERROR_OCCURRED,
)

_STARTED = "started"
_RESOLVED = "resolved"
_NOT_APPLICABLE = "not-applicable"
_VALID = "valid"
_ALLOWING = frozenset({"pass", "allowed"})
_PASSING_STEP = frozenset({"pass", _NOT_APPLICABLE})
_AGENT_INVOCABLE_FUNCTIONS = frozenset({"resolve-step", "resolve-step-model"})
_ATTRIBUTION_FLAGS = ("--session-id", "--parent-session-id")
# The host's bound models are the binding's answer (core function 4), never an agent's.
_BOUND_MODEL_FLAG = "--bound-model"
_MODEL_FUNCTION = "resolve-step-model"
_TURN_MARKER = "-t"
_HARNESS_MENTION = re.compile(r"harness\.py", re.IGNORECASE)
# ONE plain invocation: `[&] <python|python3|py|abs interpreter> [-3[.x]] <harness.py> <fn>
# [--flag [value]]...` — no chaining, piping, redirection, substitution or subexpression.
_PLAIN = r"""[^\s'";|&`$()<>{}]"""
_QUOTED = r"""(?:'[^'`$;|&<>]*'|"[^"`$;|&<>]*")"""
_STRICT_HARNESS_COMMAND = re.compile(
    rf"""^\s*(?:&\s*)?(?P<interpreter>{_QUOTED}|{_PLAIN}+)(?:\s+-[0-9][0-9.]*)?"""
    rf"""\s+(?P<harness>{_QUOTED}|{_PLAIN}+)\s+(?P<function>[a-z][a-z0-9-]*)"""
    rf"""(?:\s+--[a-z][a-z0-9-]*(?:\s+(?!--)(?:{_QUOTED}|{_PLAIN}+))?)*\s*$"""
)
_TOKEN = re.compile(rf"{_QUOTED}|{_PLAIN}+")
_TOKEN_TRIM = "'\"();|&<>`"

# The deny reason is the ONLY preToolUse field the runtime shows the agent, so it carries the
# harness's answer; these prefixes tell a result from a refusal of the request itself.
HARNESS_RESULT = "SAFe agentic harness RESULT"
HARNESS_REJECTED = "SAFe agentic harness REJECTED"
_RESULT_DIRECTIVE = (
    "this is the successful answer to your harness command, not an error. The harness hook "
    "ran '{function}' for your session itself, so your shell did not run the line (the host "
    "shows that as a denial). Act on this report; do not re-issue the command to get it again."
)


@dataclass(frozen=True)
class HarnessRequest:
    """An accepted agent request for an agent-invocable harness function."""

    function: str
    inquiry: Mapping[str, str]


Decision = Mapping[str, Any] | None
Diagnostic = Callable[[str], None]


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


class Adapter:
    """Orchestrate one host firing across the harness command API."""

    def __init__(
        self,
        binding: ToolBinding,
        tracker: SessionTracker,
        context_renderer: ContextRenderer,
        runner: CommandRunner,
        workspace_dir: str,
        invocation: HarnessInvocation,
        diagnostic: Diagnostic = _stderr,
    ) -> None:
        self._binding = binding
        self._tracker = tracker
        self._context = context_renderer
        self._runner = runner
        self._workspace_dir = workspace_dir
        self._invocation = invocation
        self._diagnostic = diagnostic
        self._workspace_name = host_basename(workspace_dir) if workspace_dir else ""
        self._handlers: Mapping[str, Callable[[HookInput], Decision]] = {
            SESSION_START: self._announce_harness,
            USER_PROMPT_SUBMITTED: self._open_turn,
            USER_PROMPT_TRANSFORMED: self._open_turn,
            SUBAGENT_START: self._open_step,
            PRE_TOOL_USE: self._gate_tool_calls,
            POST_TOOL_USE: self._settle_tool_call,
            POST_TOOL_USE_FAILURE: self._settle_tool_call,
            AGENT_STOP: self._close_agent,
            SUBAGENT_STOP: self._close_subagent,
            SESSION_END: self._close_host_session,
            ERROR_OCCURRED: self._report_error,
        }

    def _announce_harness(self, hook: HookInput) -> Decision:
        """Tell every session the one harness line it may run; opens nothing, records nothing.

        Fires for EVERY session in the folder (title summarizers included), so it is stateless:
        governance starts at H0, which only an orchestrator's scoped hooks reach.
        """
        return renderer.additional_context(self._invocation.hint())

    def handle(self, hook: HookInput) -> Decision:
        handler = self._handlers.get(hook.event)
        return handler(hook) if handler is not None else None

    # H0 -------------------------------------------------------------------------------

    def _open_turn(self, hook: HookInput) -> Decision:
        """H0 — register an orchestrator's turn, and inject its workflow context once.

        Agent-scoped: only an orchestrator's own frontmatter hook carries `--agent`, so a
        background session the app spawns (title summarizers) never reaches here with an
        agent and creates no state.
        """
        host_id = hook.host_id
        if not hook.scoping_agent or not host_id:
            return None
        record = self._tracker.read(host_id)
        if record is None:
            record = self._register_turn(hook, host_id)
            if record is None:
                return None
        if hook.event != USER_PROMPT_TRANSFORMED or record.context_injected:
            return None
        transformed = hook.payload.get("transformedPrompt")
        if not isinstance(transformed, str) or not transformed:
            return None
        context, resolved = self._resolve_context(record, inline=True)
        if resolved:
            self._tracker.write(host_id, record.with_context_injected())
        if not context:
            return None
        return renderer.transformed_prompt(context, transformed)

    def _register_turn(self, hook: HookInput, host_id: str) -> SessionRecord | None:
        stamp = hook.timestamp or str(time.time_ns() // 1_000_000)
        session_id = f"{host_id}{_TURN_MARKER}{sanitize_identifier(stamp)}"
        report = self._run_safely(
            "start-session",
            {"agent": hook.scoping_agent, "sessionId": session_id, "parentSessionId": None},
        )
        if report is None or read_status(report) != _STARTED:
            return None
        parent = hook.raw_parent_host_session_id
        record = SessionRecord(
            session_id=session_id,
            agent=str(hook.scoping_agent),
            origin=TURN_ORIGIN,
            parent_host_id=sanitize_identifier(parent) if parent else None,
        )
        self._tracker.write(host_id, record)
        return record

    # H1 -------------------------------------------------------------------------------

    def _open_step(self, hook: HookInput) -> Decision:
        """H1 — register the dispatched step under its dispatcher; prepend its context."""
        parent_host = hook.host_id
        parent = self._tracker.read(parent_host) if parent_host else None
        if parent is None:
            return None
        agent_id = self._binding.probe(hook.payload, self._binding.host_step_session_keys)
        actor = self._binding.probe(hook.payload, self._binding.host_step_actor_keys)
        if agent_id is None or actor is None:
            return None
        step_host = sanitize_identifier(str(agent_id))
        report = self._run_safely(
            "start-session",
            {
                "agent": str(actor),
                "sessionId": step_host,
                "parentSessionId": parent.session_id,
            },
        )
        if report is None or read_status(report) != _STARTED:
            return None
        record = SessionRecord(
            session_id=step_host,
            agent=str(actor),
            origin=STEP_ORIGIN,
            parent_session_id=parent.session_id,
            parent_host_id=parent_host,
        )
        context, resolved = self._resolve_context(record, inline=True)
        self._tracker.write(step_host, record.with_context_injected() if resolved else record)
        if not context:
            return None
        return {"additionalContext": context}

    # H2 / H3 / H4 ---------------------------------------------------------------------

    def _gate_tool_calls(self, hook: HookInput) -> Decision:
        """Decide a (possibly batched) `preToolUse`: deny if ANY call must be denied."""
        host_id = hook.host_id
        if not host_id:
            return renderer.deny(["the tool call carries no host session id"])
        record = self._tracker.read(host_id)
        if record is None:
            return self._gate_ungoverned(hook)
        try:
            calls = hook.read_tool_calls()
        except MalformedPayloadError as failure:
            return renderer.deny([f"unreadable tool-call payload: {failure}"])
        reasons: list[str] = []
        requests: list[HarnessRequest] = []
        for call in calls:
            reason, request = self._gate_tool_call(hook, record, call)
            if reason:
                reasons.append(reason)
            if request is not None:
                requests.append(request)
        if not reasons and requests and len(calls) > 1:
            reasons.append(
                f"{HARNESS_REJECTED}: a harness command must be issued alone, not batched "
                "with other tool calls"
            )
        if reasons:
            return renderer.deny(reasons)
        if requests:
            return self._answer_harness_request(record, requests[0])
        return None

    def _gate_ungoverned(self, hook: HookInput) -> Decision:
        """Outside a governed session everything passes — except a harness command, which
        could only run unattributed: say why instead of letting the harness fail obscurely."""
        try:
            calls = hook.read_tool_calls()
        except MalformedPayloadError:
            return None
        for call in calls:
            if not (
                self._binding.is_mediated_command_tool(call.name) and isinstance(call.args, Mapping)
            ):
                continue
            command = self._binding.probe(call.args, self._binding.mediated_command_keys)
            if isinstance(command, str) and _HARNESS_MENTION.search(command):
                return renderer.deny(
                    [
                        "no governed harness session is open for this agent: the harness "
                        "commands run only inside an orchestrator session the harness hooks "
                        "opened (is this an orchestrator agent, and is the folder trusted?)"
                    ]
                )
        return None

    def _gate_tool_call(
        self, hook: HookInput, record: SessionRecord, call: ToolCall
    ) -> tuple[str | None, HarnessRequest | None]:
        if self._binding.is_dispatch_tool(call.name):
            return self._gate_dispatch(record, call), None
        if self._binding.resolve_write_action(call.name, call.args) is not None:
            return self._gate_write(hook, record, call), None
        if self._binding.is_mediated_command_tool(call.name) or self._binding.is_guarded_shell_tool(
            call.name
        ):
            return self._gate_shell(hook, record, call)
        return None, None

    def _gate_dispatch(self, record: SessionRecord, call: ToolCall) -> str | None:
        """H2 — the step's preconditions gate its dispatch; async dispatch is refused."""
        if isinstance(call.args, Mapping):
            mode = self._binding.probe(call.args, self._binding.dispatch_mode_keys)
            if isinstance(mode, str) and mode in self._binding.async_dispatch_modes:
                return (
                    f"'{call.name}' in mode '{mode}' is not supported under the harness: "
                    "dispatch steps synchronously so their return can be evaluated"
                )
        report = self._run_safely(
            "check-step-preconditions",
            {"sessionId": record.session_id, "parentSessionId": None},
        )
        if report is None:
            return "check-step-preconditions unavailable"
        status = read_status(report)
        if status == _NOT_APPLICABLE or status in _ALLOWING:
            return None
        return renderer.describe_report(report)

    def _gate_write(self, hook: HookInput, record: SessionRecord, call: ToolCall) -> str | None:
        """H3 — authorize EVERY target of the write; unextractable targets are denied."""
        try:
            targets = self._binding.extract_write_targets(call.name, call.args)
        except TargetExtractionError as failure:
            return f"write denied (fail closed): {failure}"
        reasons: list[str] = []
        for path, action in targets:
            report = self._run_safely(
                "check-step-authorization",
                {
                    "sessionId": record.session_id,
                    "parentSessionId": record.parent_session_id,
                    "action": action,
                    "artifactPath": relativize(path, hook.cwd, self._workspace_dir),
                },
            )
            if report is None:
                reasons.append("check-step-authorization unavailable")
            elif read_status(report) not in _ALLOWING:
                reasons.append(renderer.describe_report(report))
        return "; ".join(reasons) or None

    def _gate_shell(
        self, hook: HookInput, record: SessionRecord, call: ToolCall
    ) -> tuple[str | None, HarnessRequest | None]:
        """H4 — take a harness command as a request to the hook; guard every other shell
        command (I9)."""
        if not isinstance(call.args, Mapping):
            return f"'{call.name}' carries no argument object", None
        command_key = next(
            (
                key
                for key in self._binding.mediated_command_keys
                if isinstance(call.args.get(key), str)
            ),
            None,
        )
        if command_key is None:
            return f"'{call.name}' names no command", None
        command = str(call.args[command_key])
        if _HARNESS_MENTION.search(command) and self._binding.is_mediated_command_tool(call.name):
            return self._read_harness_request(hook, command)
        if self._binding.is_guarded_shell_tool(call.name):
            offending = self._find_guarded_path(command, hook.cwd)
            if offending is not None:
                return (
                    f"shell command references the governed workspace path '{offending}': "
                    "workspace writes go through the harness's own write boundary "
                    "(use the create/edit tools)"
                ), None
        return None, None

    def _read_harness_request(
        self, hook: HookInput, command: str
    ) -> tuple[str | None, HarnessRequest | None]:
        """Accept exactly ONE plain invocation of THE installed harness; reject all else."""
        rejected, request = self._parse_harness_request(hook, command)
        return (f"{HARNESS_REJECTED}: {rejected}", None) if rejected else (None, request)

    def _parse_harness_request(
        self, hook: HookInput, command: str
    ) -> tuple[str | None, HarnessRequest | None]:
        example = self._invocation.example("resolve-step --workflow-slug <slug>")
        match = _STRICT_HARNESS_COMMAND.match(command)
        if match is None:
            return (
                "a harness command must be one plain invocation, never chained or piped: "
                f"{example}"
            ), None
        function = match.group("function")
        if function not in _AGENT_INVOCABLE_FUNCTIONS:
            return (
                f"harness function '{function}' is not agent-invocable: only "
                "resolve-step and resolve-step-model are"
            ), None
        carried = next((flag for flag in _ATTRIBUTION_FLAGS if flag in command), None)
        if carried is not None:
            return (
                f"model-authored session attribution ('{carried}') is never accepted: the "
                "harness hook attributes the request to your session itself; run: "
                f"{self._invocation.example(function)}"
            ), None
        if _BOUND_MODEL_FLAG in command:
            return (
                f"model-authored bound models ('{_BOUND_MODEL_FLAG}') are never accepted: the "
                "adapter supplies the models this host binds; run: "
                f"{self._invocation.example(function)}"
            ), None
        interpreter = unquote(match.group("interpreter"))
        if not self._invocation.is_interpreter(interpreter):
            return (
                f"'{interpreter}' is not a Python interpreter the harness runs under: "
                f"{self._invocation.example(function)}"
            ), None
        harness = unquote(match.group("harness"))
        if not self._invocation.is_harness(harness, hook.cwd):
            return (
                f"'{harness}' is not the installed harness entry point: "
                f"{self._invocation.example(function)}"
            ), None
        inquiry: dict[str, str] = {}
        tokens = [unquote(token) for token in _TOKEN.findall(command[match.end("function") :])]
        index = 0
        while index < len(tokens):
            flag = tokens[index]
            value = tokens[index + 1] if index + 1 < len(tokens) else None
            if value is None or value.startswith("--"):
                return f"flag '{flag}' needs a value", None
            key = _camel(flag[2:])
            if key in inquiry:
                return f"flag '{flag}' is given more than once", None
            inquiry[key] = value
            index += 2
        return None, HarnessRequest(function=function, inquiry=inquiry)

    def _answer_harness_request(self, record: SessionRecord, request: HarnessRequest) -> Decision:
        """H4 — run the requested function FOR the observed session and hand its report back
        as the only preToolUse text the agent sees: the deny reason. Attribution is the
        hook's own (the record keyed by the payload's sessionId), never the agent's."""
        inquiry: dict[str, Any] = {**request.inquiry, "sessionId": record.session_id}
        if request.function == _MODEL_FUNCTION:
            inquiry["boundModels"] = sorted(self._binding.model_bindings)
        try:
            report = self._runner.run_function(request.function, inquiry)
        except HarnessCommandError as failure:
            self._diagnostic(f"[{ADAPTER_ID}] {request.function} failed: {failure}")
            refusal = failure.refusal or "the harness could not answer (see the hook diagnostics)"
            return renderer.deny([f"{HARNESS_REJECTED}: {refusal}"])
        except Exception as failure:  # noqa: BLE001 - every failure must be contained
            self._diagnostic(f"[{ADAPTER_ID}] {request.function} failed: {failure}")
            return renderer.deny(
                [f"{HARNESS_REJECTED}: the harness could not answer (see the hook diagnostics)"]
            )
        rendered = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        directive = _RESULT_DIRECTIVE.format(function=request.function)
        return renderer.deny([f"{HARNESS_RESULT} ({request.function}): {directive}\n\n{rendered}"])

    def _find_guarded_path(self, command: str, cwd: str | None) -> str | None:
        markers = [f"{self._workspace_name}/", f"{self._workspace_name}\\", "logs/", "logs\\"]
        workspace = self._workspace_dir.replace("\\", "/").rstrip("/").lower()
        for token in command.split():
            candidate = token.strip(_TOKEN_TRIM)
            if not candidate:
                continue
            if self._workspace_name and any(
                candidate.startswith(marker) or f"/{marker}" in candidate or f"\\{marker}" in candidate
                for marker in markers
            ):
                return candidate
            if candidate == self._workspace_name and self._workspace_name:
                return candidate
            if workspace and candidate.replace("\\", "/").lower().startswith(workspace):
                return candidate
        return None

    # H5 / H6 --------------------------------------------------------------------------

    def _settle_tool_call(self, hook: HookInput) -> Decision:
        host_id = hook.host_id
        record = self._tracker.read(host_id) if host_id else None
        if record is None:
            return None
        try:
            (call,) = hook.read_tool_calls()
        except (MalformedPayloadError, ValueError):
            return None
        decision: dict[str, Any] = {}
        if self._binding.is_dispatch_tool(call.name):
            decision = dict(self._settle_step(record) or {})
        elif hook.event == POST_TOOL_USE and (
            self._binding.resolve_write_action(call.name, call.args) is not None
        ):
            decision = dict(self._settle_write(hook, record, call) or {})
        if not record.context_injected and hook.event == POST_TOOL_USE:
            decision = self._append_late_context(host_id, record, decision)
        return decision or None

    def _settle_write(
        self, hook: HookInput, record: SessionRecord, call: ToolCall
    ) -> Decision:
        """H5 — the commit gate: validate the landed write set, or relay its revert."""
        try:
            targets = self._binding.extract_write_targets(call.name, call.args)
        except TargetExtractionError as failure:
            return renderer.additional_context(
                f"The SAFe agentic harness could not validate this write: {failure}."
            )
        report = self._run_safely(
            "check-step-artifact",
            {
                "sessionId": record.session_id,
                "parentSessionId": record.parent_session_id,
                "artifactPaths": [
                    relativize(path, hook.cwd, self._workspace_dir) for path, _ in targets
                ],
            },
        )
        if report is None:
            return renderer.additional_context(
                "check-step-artifact unavailable: the write could not be validated. "
                "Retry once the harness answers."
            )
        if read_status(report) == _VALID:
            return None
        return renderer.write_rejected(report)

    def _settle_step(self, record: SessionRecord) -> Decision:
        """H6 — evaluate the returned step in the DISPATCHING session."""
        report = self._run_safely(
            "check-step-postconditions",
            {"sessionId": record.session_id, "parentSessionId": None},
        )
        if report is None:
            return renderer.additional_context(
                "check-step-postconditions unavailable: the step outcome could not be "
                "evaluated. Re-resolve (resolve-step)."
            )
        if read_status(report) in _PASSING_STEP:
            return None
        return renderer.step_failed(report)

    def _append_late_context(
        self, host_id: str, record: SessionRecord, decision: dict[str, Any]
    ) -> dict[str, Any]:
        """H0 fallback — when no prompt hook could inject, the first tool result does."""
        context, resolved = self._resolve_context(record, inline=False)
        if resolved:
            self._tracker.write(host_id, record.with_context_injected())
        if not context:
            return decision
        existing = decision.get("additionalContext")
        joined = f"{existing}\n\n{context}" if existing else context
        return {**decision, **renderer.additional_context(joined)}

    # H7 -------------------------------------------------------------------------------

    def _close_agent(self, hook: HookInput) -> Decision:
        """H7 — `agentStop` ends the turn (or step) of the agent that stopped."""
        if hook.host_id:
            self._close(hook.host_id)
        return None

    def _close_subagent(self, hook: HookInput) -> Decision:
        agent_id = self._binding.probe(hook.payload, self._binding.host_step_session_keys)
        if agent_id is not None:
            self._close(sanitize_identifier(str(agent_id)))
        return None

    def _close_host_session(self, hook: HookInput) -> Decision:
        """`sessionEnd` closes the host session and every session still open below it."""
        host_id = hook.host_id
        if host_id:
            for descendant in self._tracker.descendants(host_id):
                self._close(descendant)
            self._close(host_id)
        return None

    def _close(self, host_id: str) -> None:
        record = self._tracker.read(host_id)
        if record is None:
            return
        self._run_safely("end-session", {"sessionId": record.session_id})
        self._tracker.delete(host_id)

    def _report_error(self, hook: HookInput) -> Decision:
        error = hook.payload.get("error")
        if hook.host_id and self._tracker.read(hook.host_id) is not None:
            self._diagnostic(f"[{ADAPTER_ID}] host error in a governed session: {error!r}")
        return None

    # helpers --------------------------------------------------------------------------

    def _resolve_context(self, record: SessionRecord, *, inline: bool) -> tuple[str, bool]:
        """Answer the rendered context and whether it was fully resolved."""
        scope = "step" if record.origin == STEP_ORIGIN else "workflow"
        inquiry = {"sessionId": record.session_id, "parentSessionId": record.parent_session_id}
        reports: list[Mapping[str, Any]] = []
        for kind in ("instructions", "skills"):
            report = self._run_safely(f"resolve-{scope}-{kind}", inquiry)
            if report is not None:
                reports.append(report)
        try:
            resolved = sum(read_status(report) == _RESOLVED for report in reports) == 2
            context = self._context.render(reports, inline=inline)
        except (OSError, ValueError) as failure:
            self._diagnostic(f"[{ADAPTER_ID}] context rendering failed: {failure}")
            return "", False
        # Every governed agent learns the one harness line it may run — a nested orchestrator
        # dispatched as a step needs it as much as a top-level one.
        return "\n\n".join(part for part in (self._invocation.hint(), context) if part), resolved

    def _run_safely(self, function: str, inquiry: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Run a harness function; a failure is a diagnostic, never a raw leak to the model."""
        try:
            return self._runner.run_function(function, inquiry)
        except Exception as failure:  # noqa: BLE001 - every failure must be contained
            self._diagnostic(f"[{ADAPTER_ID}] {function} failed: {failure}")
            return None


def _camel(kebab: str) -> str:
    head, *rest = kebab.split("-")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def classify_calls(binding: ToolBinding, calls: Sequence[ToolCall]) -> list[str]:
    """Answer the boundary each call of a batch maps to (diagnostics and tests)."""
    classes = []
    for call in calls:
        if binding.is_dispatch_tool(call.name):
            classes.append("H2")
        elif binding.resolve_write_action(call.name, call.args) is not None:
            classes.append("H3")
        elif binding.is_mediated_command_tool(call.name) or binding.is_guarded_shell_tool(
            call.name
        ):
            classes.append("H4")
        else:
            classes.append("-")
    return classes


__all__ = [
    "ADAPTER_ID",
    "Adapter",
    "EVENTS",
    "HARNESS_REJECTED",
    "HARNESS_RESULT",
    "HarnessRequest",
    "classify_calls",
]
