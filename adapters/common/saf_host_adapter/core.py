"""The host-agnostic adapter core: one host firing in, one host decision out.

Every host binding (`adapters/<host>/`) supplies a `HostProfile`: its event names, mapped to
the boundaries below, and its decision shapes (its stdout contract). Everything else —
classification through `tools.yaml`, session tracking, sequencing the harness function
commands, and relaying their reports — is shared here. The only edge into the harness core
is its command API (adapter spec, Adapter architecture). Framework-agent gating, step
correlation and session closure are decided INSIDE the invoked commands — this class
sequences them and relays outcomes.

Boundary map (see `def/adapter/<host>/spec.md`):

    H0  turn-opening event(s) of an orchestrator                  start-session (turn)
    H1  subagent start                                            start-session (step)
    H2  pre-tool · dispatch tool                                  check-step-preconditions
    H3  pre-tool · write tool                                     check-step-authorization
    H4  pre-tool · shell tool requesting `harness.py`             resolve-step / resolve-step-model
                                                                  (run BY the hook; never by the shell)
    H5  post-tool · write tool                                    check-step-artifact
    H6  post-tool (or its failure) · dispatch tool                check-step-postconditions
    H7  agent stop / subagent stop / session end                  end-session
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, replace
from pathlib import PurePath
from typing import Any, Callable, Mapping, Protocol, Sequence

from .binding import TargetExtractionError, ToolBinding
from .folders import make_contained_folders
from .invocation import HarnessInvocation, quoted_pattern, unquote
from .paths import host_basename, is_within, relativize, resolve_host_path, workspace_parts
from .payload import HookInput, MalformedPayloadError, ToolCall, sanitize_identifier
from .reports import ContextRenderer, describe_report, read_status
from .runner import CommandRunner, HarnessCommandError
from .tracker import (
    STEP_ORIGIN,
    TURN_ORIGIN,
    WORKER_REGISTERED,
    AmbiguousDispatchError,
    SessionRecord,
    SessionTracker,
)

Decision = Mapping[str, Any] | None
Diagnostic = Callable[[str], None]


class HostDecisions(Protocol):
    """A host's decision shapes: what its runtime reads on a hook's stdout."""

    def deny(self, reasons: Sequence[str]) -> dict[str, Any]: ...

    def announce(self, text: str) -> dict[str, Any]: ...

    def accepts_turn_context(self, hook: HookInput) -> bool: ...

    def turn_context(self, context: str, hook: HookInput) -> dict[str, Any]: ...

    def fits_turn_context(self, context: str, hook: HookInput) -> bool: ...

    def step_context(self, context: str) -> dict[str, Any]: ...

    def fits_step_context(self, context: str) -> bool: ...

    def settle_context(self, text: str) -> dict[str, Any]: ...

    def fits_settle_context(self, text: str) -> bool: ...

    def context_of(self, decision: Mapping[str, Any]) -> str | None: ...

    def write_rejected(self, report: Mapping[str, Any]) -> dict[str, Any]: ...

    def write_unvalidated(self, report: Mapping[str, Any]) -> dict[str, Any]: ...

    def step_failed(self, report: Mapping[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class HostEvents:
    """A host's event names, by the role each plays in the boundary map."""

    session_start: str
    turn_open: frozenset[str]
    turn_context: str
    subagent_start: str
    pre_tool_use: str
    post_tool_use: str
    post_tool_use_failure: str | None
    agent_stop: str
    subagent_stop: str
    session_end: str
    error_occurred: str | None = None


@dataclass(frozen=True)
class HostProfile:
    """Everything host-specific the shared core needs about one host binding.

    `correlate_dispatches`: the host's subagent-start event does not name the dispatcher,
    so the adapter keeps the dispatches it let through and attributes each started
    subagent to the one pending dispatch of its agent (fail closed when ambiguous).

    `defer_stop_closure`: another hook can veto an agent's stop on this host, and the agent
    then keeps working. A stop only marks the session stopped; the session ends when its
    end is certain: a worker at its dispatch's return (named by the returned subagent's id),
    a turn at the conversation's next prompt, every session at the host session's end.
    """

    adapter_id: str
    events: HostEvents
    decisions: HostDecisions
    correlate_dispatches: bool = False
    defer_stop_closure: bool = False


_STARTED = "started"
_RESOLVED = "resolved"
_NOT_APPLICABLE = "not-applicable"
_VALID = "valid"
_REVERTED = "reverted"
_ALLOWED = "allowed"
_ALLOWING = frozenset({"pass", _ALLOWED})
_PASSING_STEP = frozenset({"pass", _NOT_APPLICABLE})
_AGENT_INVOCABLE_FUNCTIONS = frozenset({"resolve-step", "resolve-step-model"})
PLUGIN_UPDATED = "the plugin was updated; start a new session"
_ATTRIBUTION_FLAGS = ("--session-id", "--parent-session-id")
_TURN_MARKER = "-t"
_HARNESS_MENTION = re.compile(r"harness\.py", re.IGNORECASE)
# ONE plain invocation: `[&] <python|python3|py|abs interpreter> [-3[.x]] <harness.py> <fn>
# [--flag [value]]...` — no chaining, piping, redirection, substitution or subexpression.
# A quoted token takes exactly the forms the hint renders for the host's shell (PowerShell
# on Windows, POSIX elsewhere), apostrophes included: see `invocation.quoted_pattern`.
_PLAIN = r"""[^\s'";|&`$()<>{}]"""


def _command_grammar(powershell: bool) -> tuple[re.Pattern[str], re.Pattern[str]]:
    quoted = quoted_pattern(powershell)
    strict = re.compile(
        rf"""^\s*(?:&\s*)?(?P<interpreter>{quoted}|{_PLAIN}+)(?:\s+-[0-9][0-9.]*)?"""
        rf"""\s+(?P<harness>{quoted}|{_PLAIN}+)\s+(?P<function>[a-z][a-z0-9-]*)"""
        rf"""(?:\s+--[a-z][a-z0-9-]*(?:\s+(?!--)(?:{quoted}|{_PLAIN}+))?)*\s*$"""
    )
    return strict, re.compile(rf"{quoted}|{_PLAIN}+")


_COMMAND_GRAMMARS = {flavor: _command_grammar(flavor) for flavor in (False, True)}
_TOKEN_TRIM = "'\"();|&<>`"
# A `<rev>:<path>` git object name (`HEAD:portfolio/x.md`): never a drive (`C:`), a variable
# (`$env:TEMP`) or a URL.
_GIT_REVISION_PATH = re.compile(r"^(?![A-Za-z]:)[^:/\\$]+:(?!//)(?P<path>.+)$")

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


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


class Adapter:
    """Orchestrate one host firing across the harness command API."""

    def __init__(
        self,
        host: HostProfile,
        binding: ToolBinding,
        tracker: SessionTracker,
        context_renderer: ContextRenderer,
        runner: CommandRunner,
        workspace_dir: str,
        invocation: HarnessInvocation,
        diagnostic: Diagnostic = _stderr,
        make_folders: Callable[[str, Sequence[str]], None] = make_contained_folders,
        harness_version: str | None = None,
    ) -> None:
        self._host = host
        self._events = host.events
        self._decisions = host.decisions
        self._adapter_id = host.adapter_id
        self._binding = binding
        self._harness_version = harness_version
        self._tracker = tracker
        self._context = context_renderer
        self._runner = runner
        self._workspace_dir = workspace_dir
        self._invocation = invocation
        self._diagnostic = diagnostic
        self._make_folders = make_folders
        self._workspace_name = host_basename(workspace_dir) if workspace_dir else ""
        events = self._events
        handlers: dict[str, Callable[[HookInput], Decision]] = {
            events.session_start: self._announce_harness,
            **{event: self._open_turn for event in events.turn_open},
            events.subagent_start: self._open_step,
            events.pre_tool_use: self._gate_tool_calls,
            events.post_tool_use: self._settle_tool_call,
            events.agent_stop: self._close_agent,
            events.subagent_stop: self._close_subagent,
            events.session_end: self._close_host_session,
        }
        if events.post_tool_use_failure:
            handlers[events.post_tool_use_failure] = self._settle_tool_call
        if events.error_occurred:
            handlers[events.error_occurred] = self._report_error
        self._handlers: Mapping[str, Callable[[HookInput], Decision]] = handlers
        # Closure (H7) and diagnostics run whatever version opened the session: core
        # function 11 is exempt from the version check, and the announce reads no state.
        self._version_exempt = frozenset(
            name
            for name in (
                events.session_start,
                events.subagent_stop,
                events.agent_stop,
                events.session_end,
                events.error_occurred,
            )
            if name
        )

    def _announce_harness(self, hook: HookInput) -> Decision:
        """Tell every session the one harness line it may run; opens nothing, records nothing.

        Fires for EVERY session in the folder (title summarizers included), so it is stateless:
        governance starts at H0, which only an orchestrator's scoped hooks reach.
        """
        return self._decisions.announce(self._invocation.hint())

    @property
    def workspace_dir(self) -> str:
        return self._workspace_dir

    def recorded_workspace(self, hook: HookInput) -> str | None:
        """The workspace the firing's session was fixed to when its record was created.

        Adapter spec, Session identity: later firings never re-resolve it. A worker session
        about to open (H1) takes its dispatcher's.
        """
        host_ids = [hook.host_id]
        if hook.event == self._events.subagent_start:
            host_ids[:0] = [hook.dispatcher_host_id, hook.root_host_id]
        for host_id in host_ids:
            record = self._tracker.read(host_id) if host_id else None
            if record is not None and record.workspace:
                return record.workspace
        return None

    def handle(self, hook: HookInput) -> Decision:
        handler = self._handlers.get(hook.event)
        if handler is None:
            return None
        if hook.event not in self._version_exempt:
            refusal = self._refuse_other_version(hook)
            if refusal is not None:
                return refusal if hook.event == self._events.pre_tool_use else None
        return handler(hook)

    def _refuse_other_version(self, hook: HookInput) -> Decision:
        """Deny a firing whose session another harness version opened.

        Adapter spec, Session identity: "the plugin was updated; start a new session".
        Closure (H7) still runs: core function 11 is exempt from the version check.
        """
        host_id = (
            hook.dispatcher_host_id
            if hook.event == self._events.subagent_start
            else hook.host_id
        )
        if self._harness_version is None or not host_id:
            return None
        record = self._tracker.read(host_id)
        if record is None or record.harness_version == self._harness_version:
            return None
        self._diagnostic(
            f"[{self._adapter_id}] {hook.event}: session opened by harness "
            f"{record.harness_version or 'of an unknown version'}; installed "
            f"{self._harness_version}"
        )
        return self._decisions.deny([PLUGIN_UPDATED])

    # H0 -------------------------------------------------------------------------------

    def _open_turn(self, hook: HookInput) -> Decision:
        """H0 — register an orchestrator's turn, and inject its workflow context once.

        Agent-scoped: only an orchestrator's own frontmatter hook carries `--agent`, so a
        background session the app spawns (title summarizers) never reaches here with an
        agent and creates no state.

        A worker session is H1's, never H0's: an orchestrator agent dispatched as a step actor
        still fires its scoped hook, but it is neither re-registered nor given workflow
        context. Nor does H0 open a root session for an agent a GOVERNED session dispatched:
        that agent was opened by its dispatcher's action, so it is a step (H1) or, when H1
        could not open it, ungoverned.
        """
        host_id = hook.host_id
        if not hook.turn_agent or not host_id:
            return None
        record = self._tracker.read(host_id)
        if record is not None and record.origin == STEP_ORIGIN:
            return None
        if record is not None and record.stopped:
            # The previous turn's stop stood: the new prompt opens a new turn.
            self._close(host_id)
            record = None
        if record is None:
            dispatcher = hook.parent_host_id
            if dispatcher and dispatcher != host_id and self._tracker.read(dispatcher):
                self._diagnostic(
                    f"[{self._adapter_id}] {hook.event}: '{hook.turn_agent}' was dispatched by a "
                    "governed session but holds no worker session; it stays ungoverned"
                )
                return None
        if record is None or not record.registered:
            record = self._register_turn(hook, host_id)
            if record is None or not record.registered:
                return None
        if hook.event != self._events.turn_context or record.context_injected:
            return None
        if not self._decisions.accepts_turn_context(hook):
            return None
        context, delivered = self._resolve_context(
            record,
            inline=True,
            fits=lambda text: self._decisions.fits_turn_context(text, hook),
        )
        if delivered:
            self._tracker.write(host_id, record.with_context_injected())
        if not context:
            return None
        return self._decisions.turn_context(context, hook)

    def _register_turn(self, hook: HookInput, host_id: str) -> SessionRecord | None:
        stamp = hook.timestamp or str(time.time_ns() // 1_000_000)
        session_id = f"{host_id}{_TURN_MARKER}{sanitize_identifier(stamp)}"
        report = self._run_safely(
            "start-session",
            {"agent": hook.turn_agent, "sessionId": session_id, "parentSessionId": None},
        )
        if report is not None and read_status(report) == _NOT_APPLICABLE:
            self._tracker.delete(host_id)
            return None
        parent = hook.raw_parent_host_session_id
        self._tracker.clear_ignored_returns(host_id)
        if self._host.correlate_dispatches and hook.root_host_id:
            self._tracker.clear_pending_dispatches(hook.root_host_id)
        record = SessionRecord(
            session_id=session_id,
            agent=str(hook.turn_agent),
            origin=TURN_ORIGIN,
            parent_host_id=sanitize_identifier(parent) if parent else None,
            registration_failure=self._registration_failure(report),
            workspace=self._workspace_dir,
            harness_version=self._harness_version,
        )
        self._tracker.write(host_id, record)
        return record

    # H1 -------------------------------------------------------------------------------

    def _open_step(self, hook: HookInput) -> Decision:
        """H1 — register the dispatched step under its dispatcher; prepend its context."""
        agent_id = self._binding.probe(hook.payload, self._binding.host_worker_session_keys)
        actor = self._binding.probe(hook.payload, self._binding.host_step_actor_keys)
        parent_host = hook.dispatcher_host_id
        correlated = parent_host is None and self._host.correlate_dispatches
        if correlated:
            if agent_id is None:
                return None
            parent_host = self._correlate_dispatcher(hook, agent_id, actor)
        parent = self._tracker.read(parent_host) if parent_host else None
        if parent is None:
            return None
        if agent_id is None:
            return None
        if (
            correlated
            and self._harness_version is not None
            and parent.harness_version != self._harness_version
        ):
            # The dispatcher was correlated, not observed, so `handle` could not check its
            # version. The subagent is already starting: record it UNREGISTERED, so its
            # writes and dispatches are denied (fail closed), never left ungoverned.
            step_host = sanitize_identifier(str(agent_id))
            self._tracker.write(
                step_host,
                SessionRecord(
                    session_id=step_host,
                    agent=self._binding.agent_name(str(actor or "")),
                    origin=STEP_ORIGIN,
                    parent_session_id=parent.session_id,
                    parent_host_id=parent_host,
                    registration_failure=PLUGIN_UPDATED,
                    workspace=self._workspace_dir,
                    harness_version=self._harness_version,
                ),
            )
            self._note_worker(hook, agent_id, parent_host, "refused")
            return None
        if actor is None:
            # Never registered as the step's worker, yet attributable: its return must not
            # conclude the step (H6).
            self._ignore_return(parent_host, parent, sanitize_identifier(str(agent_id)))
            self._note_worker(hook, agent_id, parent_host, "unattributed")
            return None
        actor = self._binding.agent_name(str(actor))
        step_host = sanitize_identifier(str(agent_id))
        record = SessionRecord(
            session_id=step_host,
            agent=str(actor),
            origin=STEP_ORIGIN,
            parent_session_id=parent.session_id,
            parent_host_id=parent_host,
            workspace=self._workspace_dir,
            harness_version=self._harness_version,
        )
        if not parent.registered:
            self._tracker.write(
                step_host,
                replace(
                    record,
                    registration_failure="its dispatcher's session is not registered",
                ),
            )
            self._note_worker(hook, agent_id, parent_host, "refused")
            return None
        if self._host.correlate_dispatches:
            self._tracker.remember_dispatcher(step_host, parent_host)
        report = self._run_safely(
            "start-session",
            {
                "agent": str(actor),
                "sessionId": step_host,
                "parentSessionId": parent.session_id,
            },
        )
        if report is not None and read_status(report) == _NOT_APPLICABLE:
            self._ignore_return(parent_host, parent, step_host)
            self._note_worker(hook, agent_id, parent_host, "not-applicable")
            return None
        failure = self._registration_failure(report)
        if failure is not None:
            self._tracker.write(step_host, replace(record, registration_failure=failure))
            self._ignore_return(parent_host, parent, step_host)
            self._note_worker(hook, agent_id, parent_host, "refused")
            return None
        self._note_worker(hook, agent_id, parent_host, WORKER_REGISTERED)
        context, delivered = self._resolve_context(
            record,
            inline=True,
            fits=self._decisions.fits_step_context,
        )
        self._tracker.write(step_host, record.with_context_injected() if delivered else record)
        if not context:
            return None
        return self._decisions.step_context(context)

    def _correlate_dispatcher(self, hook: HookInput, agent_id: Any, actor: Any) -> str | None:
        """H1 on a host whose subagent start names no dispatcher: claim the one pending
        dispatch of this agent. An ambiguous start is recorded UNREGISTERED under the
        conversation (fail closed: its writes and dispatches are denied)."""
        root = hook.root_host_id
        if not root or actor is None:
            return None
        try:
            return self._tracker.claim_pending_dispatch(root, str(actor))
        except AmbiguousDispatchError as failure:
            self._diagnostic(f"[{self._adapter_id}] {hook.event}: {failure}")
            conversation = self._tracker.read(root)
            if conversation is None:
                return None
            step_host = sanitize_identifier(str(agent_id))
            self._tracker.write(
                step_host,
                SessionRecord(
                    session_id=step_host,
                    agent=self._binding.agent_name(str(actor)),
                    origin=STEP_ORIGIN,
                    parent_session_id=conversation.session_id,
                    parent_host_id=root,
                    registration_failure=str(failure),
                    workspace=self._workspace_dir,
                    harness_version=self._harness_version,
                ),
            )
            self._note_worker(hook, agent_id, None, "ambiguous")
            return None

    def _note_worker(
        self, hook: HookInput, agent_id: Any, dispatcher_host: str | None, outcome: str
    ) -> None:
        """Keep H1's outcome for this subagent where the host reports, at the dispatch's
        return, which subagent returned (H6 decides by that identity)."""
        root = hook.root_host_id
        if self._binding.dispatch_result_agent_keys and root and agent_id is not None:
            self._tracker.record_worker(
                root, sanitize_identifier(str(agent_id)), dispatcher_host, outcome
            )

    def _ignore_return(
        self, dispatcher_host: str, dispatcher: SessionRecord, step_host: str
    ) -> None:
        """Mark this sub-agent's return as not concluding the dispatcher's step (H6).

        Adapter spec H6: a dispatch return whose sub-agent's worker-session registration
        was refused, or that was never registered as the step's worker, does not trigger
        `check-step-postconditions`; the step stays in flight for its bound worker session.
        """
        if dispatcher.registered:
            self._tracker.mark_ignored_return(dispatcher_host, step_host)

    # H2 / H3 / H4 ---------------------------------------------------------------------

    def _gate_tool_calls(self, hook: HookInput) -> Decision:
        """Decide a (possibly batched) `preToolUse`: deny if ANY call must be denied."""
        host_id = hook.host_id
        if not host_id:
            return self._decisions.deny(["the tool call carries no host session id"])
        record = self._tracker.read(host_id)
        if record is None:
            return self._gate_ungoverned(hook)
        try:
            calls = hook.read_tool_calls()
        except MalformedPayloadError as failure:
            return self._decisions.deny([f"unreadable tool-call payload: {failure}"])
        reasons: list[str] = []
        requests: list[HarnessRequest] = []
        creations: list[str] = []
        for call in calls:
            reason, request = self._gate_tool_call(hook, record, call, creations)
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
            return self._decisions.deny(reasons)
        if requests:
            return self._answer_harness_request(record, requests[0])
        self._record_dispatches(hook, calls)
        self._prepare_parent_folders(hook, creations)
        return None

    def _record_dispatches(self, hook: HookInput, calls: Sequence[ToolCall]) -> None:
        """Keep every dispatch this firing lets through, for H1 to correlate (hosts whose
        subagent start names no dispatcher)."""
        root, host_id = hook.root_host_id, hook.host_id
        if not (self._host.correlate_dispatches and root and host_id):
            return
        for call in calls:
            if not (self._binding.is_dispatch_tool(call.name) and isinstance(call.args, Mapping)):
                continue
            actor = self._binding.probe(call.args, self._binding.dispatch_agent_keys)
            if isinstance(actor, str) and actor:
                self._tracker.add_pending_dispatch(root, call.call_id, host_id, actor)

    def _prepare_parent_folders(self, hook: HookInput, paths: Sequence[str]) -> None:
        """H3 host adaptation — the runtime's `create` refuses a missing parent folder, and
        no other tool offered to every model creates one. Once the WHOLE firing is allowed,
        create the missing folders of each harness-allowed `create` target inside the
        workspace: folders only, no content, no commit (git does not track empty folders).

        Containment is checked lexically here and enforced again on the REAL filesystem by
        `make_folders`, which refuses any symlink or junction under the workspace."""
        for raw in paths:
            if not is_within(raw, hook.cwd, self._workspace_dir):
                continue
            resolved = resolve_host_path(raw, hook.cwd, self._workspace_dir)
            if type(resolved) is not type(PurePath()):
                continue
            parts = workspace_parts(resolved.parent, self._workspace_dir)
            if parts is None:
                continue
            try:
                self._make_folders(self._workspace_dir, parts)
            except OSError as failure:
                self._diagnostic(
                    f"[{self._adapter_id}] could not create the folder of '{raw}': {failure}"
                )

    def _gate_ungoverned(self, hook: HookInput) -> Decision:
        """Outside a governed session everything passes — except a harness command, which
        could only run unattributed: say why instead of letting the harness fail obscurely."""
        try:
            calls = hook.read_tool_calls()
        except MalformedPayloadError:
            return None
        if hook.root_host_id and self._tracker.read(hook.root_host_id) is not None:
            # An ungoverned agent inside a governed conversation: its dispatches still
            # count when a started subagent is correlated to its dispatcher.
            self._record_dispatches(hook, calls)
        for call in calls:
            if not (
                self._binding.is_mediated_command_tool(call.name) and isinstance(call.args, Mapping)
            ):
                continue
            command = self._binding.probe(call.args, self._binding.mediated_command_keys)
            if isinstance(command, str) and _HARNESS_MENTION.search(command):
                return self._decisions.deny(
                    [
                        "no governed harness session is open for this agent: the harness "
                        "commands run only inside an orchestrator session the harness hooks "
                        "opened (is this an orchestrator agent, and is the folder trusted?)"
                    ]
                )
        return None

    def _gate_tool_call(
        self, hook: HookInput, record: SessionRecord, call: ToolCall, creations: list[str]
    ) -> tuple[str | None, HarnessRequest | None]:
        if not record.registered:
            return self._gate_unregistered_call(hook, record, call), None
        if self._binding.is_dispatch_tool(call.name):
            return self._gate_dispatch(record, call), None
        if self._binding.resolve_write_action(call.name, call.args) is not None:
            return self._gate_write(hook, record, call, creations), None
        if self._binding.is_mediated_command_tool(call.name) or self._binding.is_guarded_shell_tool(
            call.name
        ):
            return self._gate_shell(hook, record, call)
        return None, None

    def _gate_unregistered_call(
        self, hook: HookInput, record: SessionRecord, call: ToolCall
    ) -> str | None:
        """A session whose `start-session` failed is not ungoverned: its writes, dispatches
        and harness requests are denied (fail closed); I9 still guards its shell."""
        reason = (
            f"this session is not registered with the harness ({record.registration_failure}): "
            "its writes and dispatches are denied until a registration succeeds (fail closed)"
        )
        if self._binding.is_dispatch_tool(call.name) or (
            self._binding.resolve_write_action(call.name, call.args) is not None
        ):
            return reason
        if self._binding.is_mediated_command_tool(call.name) or self._binding.is_guarded_shell_tool(
            call.name
        ):
            rejected, request = self._gate_shell(hook, record, call)
            return f"{HARNESS_REJECTED}: {reason}" if request is not None else rejected
        return None

    def _gate_dispatch(self, record: SessionRecord, call: ToolCall) -> str | None:
        """H2 — the step's preconditions gate its dispatch; async dispatch is refused."""
        if isinstance(call.args, Mapping):
            mode = self._binding.probe(call.args, self._binding.dispatch_mode_keys)
            if isinstance(mode, str) and mode in self._binding.async_dispatch_modes:
                return (
                    f"'{call.name}' in mode '{mode}' is not supported under the harness: "
                    "dispatch steps synchronously so their return can be evaluated"
                )
            for flag in self._binding.sync_dispatch_flag_keys:
                if call.args.get(flag) is not False:
                    return (
                        f"'{call.name}' must be dispatched synchronously under the harness: "
                        f"set '{flag}': false so the step's return can be evaluated"
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
        return describe_report(report)

    def _gate_write(
        self, hook: HookInput, record: SessionRecord, call: ToolCall, creations: list[str]
    ) -> str | None:
        """H3 — authorize EVERY target of the write; unextractable targets are denied.

        Each `allowed` `create` target of a non-patch tool is collected into `creations`
        (a patch's `*** Add File:` creates its own folders)."""
        try:
            targets = self._binding.extract_write_targets(call.name, call.args)
        except TargetExtractionError as failure:
            return f"write denied (fail closed): {failure}"
        reasons: list[str] = []
        for path, action in targets:
            if call.name in self._binding.upsert_tools:
                action = self._upsert_action(path, hook.cwd)
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
                reasons.append(describe_report(report))
            elif (
                action == "create"
                and read_status(report) == _ALLOWED
                and call.name not in self._binding.patch_tools
            ):
                creations.append(path)
        return "; ".join(reasons) or None

    def _upsert_action(self, raw: str, cwd: str | None) -> str:
        """An upsert tool creates a missing file and updates an existing one."""
        resolved = resolve_host_path(raw, cwd, self._workspace_dir)
        return "update" if os.path.exists(str(resolved)) else "create"

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
        powershell = self._invocation.windows
        strict, token_pattern = _COMMAND_GRAMMARS[powershell]
        match = strict.match(command)
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
        interpreter = unquote(match.group("interpreter"), powershell)
        if not self._invocation.is_interpreter(interpreter):
            return (
                f"'{interpreter}' is not a Python interpreter the harness runs under: "
                f"{self._invocation.example(function)}"
            ), None
        harness = unquote(match.group("harness"), powershell)
        if not self._invocation.is_harness(harness, hook.cwd):
            return (
                f"'{harness}' is not the installed harness entry point: "
                f"{self._invocation.example(function)}"
            ), None
        inquiry: dict[str, str] = {}
        tokens = [
            unquote(token, powershell)
            for token in token_pattern.findall(command[match.end("function") :])
        ]
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
        hook's own (the record keyed by the payload's sessionId, with the parent it tracks),
        never the agent's."""
        inquiry = {
            **request.inquiry,
            "sessionId": record.session_id,
            "parentSessionId": record.parent_session_id,
        }
        try:
            report = self._runner.run_function(request.function, inquiry)
        except HarnessCommandError as failure:
            self._diagnostic(f"[{self._adapter_id}] {request.function} failed: {failure}")
            refusal = failure.refusal or "the harness could not answer (see the hook diagnostics)"
            return self._decisions.deny([f"{HARNESS_REJECTED}: {refusal}"])
        except Exception as failure:  # noqa: BLE001 - every failure must be contained
            self._diagnostic(f"[{self._adapter_id}] {request.function} failed: {failure}")
            return self._decisions.deny(
                [f"{HARNESS_REJECTED}: the harness could not answer (see the hook diagnostics)"]
            )
        rendered = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
        directive = _RESULT_DIRECTIVE.format(function=request.function)
        directive += self._host_model_directive(report)
        return self._decisions.deny(
            [f"{HARNESS_RESULT} ({request.function}): {directive}\n\n{rendered}"]
        )

    def _host_model_directive(self, report: Mapping[str, Any]) -> str:
        """`resolve-step-model` names a canonical profile slug; where this host's binding
        dispatches it under another id (`models.yaml`), name that id, so the agent passes
        what the host's dispatch tool accepts."""
        if (report.get("context") or {}).get("function") != "resolve-step-model":
            return ""
        slug = (report.get("profile") or {}).get("slug")
        host_model = self._binding.model_bindings.get(str(slug)) if slug else None
        if not host_model or host_model == slug:
            return ""
        return (
            f" On this host, dispatch the resolved profile '{slug}' with the model "
            f"'{host_model}': pass exactly '{host_model}' as the dispatch tool's model argument."
        )

    def _find_guarded_path(self, command: str, cwd: str | None) -> str | None:
        """I9 — the first token naming the workspace or `logs/`: by name, by absolute prefix,
        or by a relative path that resolves into the workspace.

        A relative path resolves against the event's `cwd`. A token is taken as a path when
        it has a separator or a leading `.`, when it exists, or when it follows `--`. A git
        `<rev>:<path>` object name counts as a path from the repository root: the workspace
        when `cwd` is inside it.
        """
        pathspecs_only = False
        for token in command.split():
            candidate = token.strip(_TOKEN_TRIM)
            if not candidate:
                if any(separator in token for separator in ";|&"):
                    pathspecs_only = False
                continue
            if candidate == "--":
                pathspecs_only = True
                continue
            if self._names_guarded_path(candidate, cwd, pathspecs_only):
                return candidate
        return None

    def _names_guarded_path(self, candidate: str, cwd: str | None, pathspec: bool) -> bool:
        references = [(candidate, cwd, pathspec)]
        is_option = candidate.startswith("-")
        if is_option and "=" in candidate:
            # `--output="x"`: the token trim left the value's opening quote on.
            references.append((candidate.split("=", 1)[1].strip(_TOKEN_TRIM), cwd, False))
        # An option is never a `<rev>:<path>` object name (`--format=%h:%s`).
        revision = None if is_option else _GIT_REVISION_PATH.match(candidate)
        if revision is not None:
            path = revision.group("path")
            root = (
                self._workspace_dir
                if not path.startswith(".") and is_within(cwd or "", None, self._workspace_dir)
                else cwd
            )
            references.append((path, root, True))
        return any(
            self._names_by_text(reference) or self._names_by_resolution(reference, anchor, explicit)
            for reference, anchor, explicit in references
        )

    def _names_by_text(self, reference: str) -> bool:
        name = self._workspace_name
        markers = [f"{name}/", f"{name}\\", "logs/", "logs\\"]
        if name and (
            reference == name
            or any(
                reference.startswith(marker) or f"/{marker}" in reference or f"\\{marker}" in reference
                for marker in markers
            )
        ):
            return True
        workspace = self._workspace_dir.replace("\\", "/").rstrip("/").lower()
        return bool(workspace) and reference.replace("\\", "/").lower().startswith(workspace)

    def _names_by_resolution(self, reference: str, anchor: str | None, explicit: bool) -> bool:
        if not anchor or not reference or reference[0] in "-$~" or "://" in reference:
            return False
        if not (
            explicit
            or "/" in reference
            or "\\" in reference
            or reference.startswith(".")
            or os.path.exists(str(resolve_host_path(reference, anchor, self._workspace_dir)))
        ):
            return False
        return is_within(reference, anchor, self._workspace_dir)

    # H5 / H6 --------------------------------------------------------------------------

    def _settle_tool_call(self, hook: HookInput) -> Decision:
        host_id = hook.host_id
        try:
            (call,) = hook.read_tool_calls()
        except (MalformedPayloadError, ValueError):
            return None
        is_dispatch = self._binding.is_dispatch_tool(call.name)
        never_started = False
        if is_dispatch and self._host.correlate_dispatches and hook.root_host_id:
            # Whatever the dispatching session is, its returned dispatch is no longer pending.
            # Still pending means no subagent ever started for it.
            never_started = self._tracker.drop_pending_dispatch(hook.root_host_id, call.call_id)
        record = self._tracker.read(host_id) if host_id else None
        if record is None or not record.registered:
            return None
        decision: dict[str, Any] = {}
        if is_dispatch and never_started:
            # A dispatch that failed before its subagent started delivered no step: it must
            # not evaluate the step another (still running) worker is bound to.
            self._diagnostic(
                f"[{self._adapter_id}] {hook.event}: dispatch '{call.call_id}' returned before "
                "any subagent started: the step is not evaluated"
            )
        elif is_dispatch:
            decision = dict(self._settle_dispatch(hook, host_id, record) or {})
        elif hook.event == self._events.post_tool_use and (
            self._binding.resolve_write_action(call.name, call.args) is not None
        ):
            decision = dict(self._settle_write(hook, record, call) or {})
        if not record.context_injected and hook.event == self._events.post_tool_use:
            decision = self._append_late_context(host_id, record, decision)
        return decision or None

    def _settle_dispatch(
        self, hook: HookInput, host_id: str, record: SessionRecord
    ) -> Decision:
        """H6 — decide whether this dispatch return concludes the dispatcher's step.

        A host that reports which subagent returned (`dispatchResultAgentKeys`) is decided by
        that observed identity: only the subagent H1 registered as this dispatcher's worker
        concludes the step; a background return concludes nothing. Other hosts rely on the
        ignored-return markers, which follow the host's event order.
        """
        if not self._binding.dispatch_result_agent_keys:
            if self._tracker.consume_ignored_return(host_id):
                return None
            return self._settle_step(record)
        result = self._binding.probe(hook.payload, self._binding.dispatch_result_keys)
        result = result if isinstance(result, Mapping) else {}
        status = self._binding.probe(result, self._binding.dispatch_result_status_keys)
        if isinstance(status, str) and status in self._binding.async_dispatch_statuses:
            self._diagnostic(f"[{self._adapter_id}] {hook.event}: a dispatch returned '{status}'")
            return self._decisions.settle_context(
                "This dispatch returned before its subagent ended (it runs in the background), "
                "so the step was not evaluated. Dispatch steps synchronously "
                "(run_in_background: false); re-resolve (resolve-step) once the subagent ends."
            )
        returned = self._binding.probe(result, self._binding.dispatch_result_agent_keys)
        if returned is None:
            # No identity in this return: fall back on the event-order markers.
            if self._tracker.consume_ignored_return(host_id):
                return None
            return self._settle_step(record)
        agent = sanitize_identifier(str(returned))
        self._tracker.discard_ignored_return(host_id, agent)
        worker = (
            self._tracker.take_worker(hook.root_host_id, agent) if hook.root_host_id else None
        )
        if self._host.defer_stop_closure:
            # The dispatch returned: the subagent's end is now certain (step-ended follows the
            # worker session's end, core function 10).
            self._close(agent)
        if (
            worker is None
            or worker.get("outcome") != WORKER_REGISTERED
            or worker.get("dispatcher") != host_id
        ):
            self._diagnostic(
                f"[{self._adapter_id}] {hook.event}: subagent '{agent}' returned but was not "
                f"registered as this dispatcher's worker "
                f"({(worker or {}).get('outcome', 'never seen')}): the step stays in flight"
            )
            return None
        return self._settle_step(record)

    def _settle_write(
        self, hook: HookInput, record: SessionRecord, call: ToolCall
    ) -> Decision:
        """H5 — the commit gate: validate the landed write set, or relay its revert."""
        try:
            targets = self._binding.extract_write_targets(call.name, call.args)
        except TargetExtractionError as failure:
            return self._decisions.settle_context(
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
            return self._decisions.settle_context(
                "check-step-artifact unavailable: the write could not be validated. "
                "Retry once the harness answers."
            )
        status = read_status(report)
        if status == _VALID:
            return None
        if status == _REVERTED:
            return self._decisions.write_rejected(report)
        return self._decisions.write_unvalidated(report)

    def _settle_step(self, record: SessionRecord) -> Decision:
        """H6 — evaluate the returned step in the DISPATCHING session."""
        report = self._run_safely(
            "check-step-postconditions",
            {"sessionId": record.session_id, "parentSessionId": None},
        )
        if report is None:
            return self._decisions.settle_context(
                "check-step-postconditions unavailable: the step outcome could not be "
                "evaluated. Re-resolve (resolve-step)."
            )
        if read_status(report) in _PASSING_STEP:
            return None
        return self._decisions.step_failed(report)

    def _append_late_context(
        self, host_id: str, record: SessionRecord, decision: dict[str, Any]
    ) -> dict[str, Any]:
        """H0 fallback — when no prompt hook could inject, the first tool result does.

        Its `additionalContext` is capped at 10 KB, so the context goes as load directives,
        and is marked injected only when it fits whole next to what the decision already says.
        """
        existing = self._decisions.context_of(decision)

        def joined(text: str) -> str:
            return f"{existing}\n\n{text}" if existing else text

        context, delivered = self._resolve_context(
            record,
            inline=False,
            fits=lambda text: self._decisions.fits_settle_context(joined(text)),
            truncate=True,
        )
        if delivered:
            self._tracker.write(host_id, record.with_context_injected())
        if not context:
            return decision
        return {**decision, **self._decisions.settle_context(joined(context))}

    # H7 -------------------------------------------------------------------------------

    def _close_agent(self, hook: HookInput) -> Decision:
        """H7 — `agentStop` ends the turn (or step) of the agent that stopped."""
        if hook.host_id:
            if self._host.defer_stop_closure:
                self._mark_stopped(hook.host_id)
            else:
                self._close(hook.host_id)
            if self._host.correlate_dispatches and hook.host_id == hook.root_host_id:
                # The conversation's turn ended: no dispatch of it is still starting.
                self._tracker.clear_pending_dispatches(hook.host_id)
        return None

    def _close_subagent(self, hook: HookInput) -> Decision:
        agent_id = self._binding.probe(hook.payload, self._binding.host_worker_session_keys)
        if agent_id is not None:
            step_host = sanitize_identifier(str(agent_id))
            dispatcher = hook.dispatcher_host_id
            if dispatcher is None and self._host.correlate_dispatches:
                dispatcher = self._tracker.dispatcher_of(step_host)
                self._tracker.forget_dispatcher(step_host)
            if dispatcher:
                # H6: a sub-agent recorded as ignored at H1 has now stopped, so the
                # dispatcher's next return is its own (host fact 6) and is not evaluated.
                self._tracker.arm_ignored_return(dispatcher, step_host)
            if self._host.defer_stop_closure:
                self._mark_stopped(step_host)
            else:
                self._close(step_host)
        return None

    def _mark_stopped(self, host_id: str) -> None:
        """Keep a stopped session governed until its end is certain (defer_stop_closure)."""
        record = self._tracker.read(host_id)
        if record is not None and not record.stopped:
            self._tracker.write(host_id, record.with_stopped())

    def _close_host_session(self, hook: HookInput) -> Decision:
        """`sessionEnd` closes the host session and every session still open below it."""
        host_id = hook.host_id
        if host_id:
            for descendant in self._tracker.descendants(host_id):
                self._close(descendant)
                if self._host.correlate_dispatches:
                    self._tracker.forget_dispatcher(descendant)
            self._close(host_id)
            if self._host.correlate_dispatches:
                self._tracker.clear_pending_dispatches(hook.root_host_id or host_id)
            self._tracker.clear_workers(hook.root_host_id or host_id)
        return None

    def _close(self, host_id: str) -> None:
        record = self._tracker.read(host_id)
        if record is None:
            return
        if record.registered:
            self._run_safely("end-session", {"sessionId": record.session_id})
        self._tracker.delete(host_id)

    def _report_error(self, hook: HookInput) -> Decision:
        error = hook.payload.get("error")
        if hook.host_id and self._tracker.read(hook.host_id) is not None:
            self._diagnostic(f"[{self._adapter_id}] host error in a governed session: {error!r}")
        return None

    # helpers --------------------------------------------------------------------------

    def _resolve_context(
        self,
        record: SessionRecord,
        *,
        inline: bool,
        fits: Callable[[str], bool],
        truncate: bool = False,
    ) -> tuple[str, bool]:
        """Answer the context to inject and whether ALL of it is delivered by doing so.

        Instruction bodies are inlined when `inline` and the output `fits` the event's limit;
        otherwise they become load directives. Delivered means both resolutions answered
        `resolved` AND the injected text fits whole. A context that cannot fit is answered
        anyway when `truncate` (the renderer cuts it to the cap), else not at all.
        """
        scope = "step" if record.origin == STEP_ORIGIN else "workflow"
        inquiry = {"sessionId": record.session_id, "parentSessionId": record.parent_session_id}
        reports: list[Mapping[str, Any]] = []
        for kind in ("instructions", "skills"):
            report = self._run_safely(f"resolve-{scope}-{kind}", inquiry)
            if report is not None:
                reports.append(report)
        resolved = sum(read_status(report) == _RESOLVED for report in reports) == 2
        context = ""
        for mode in (True, False) if inline else (False,):
            try:
                rendered = self._context.render(reports, inline=mode)
            except (OSError, ValueError) as failure:
                self._diagnostic(f"[{self._adapter_id}] context rendering failed: {failure}")
                return "", False
            # Every governed agent learns the one harness line it may run — a nested
            # orchestrator dispatched as a step needs it as much as a top-level one.
            context = "\n\n".join(part for part in (self._invocation.hint(), rendered) if part)
            if fits(context):
                return context, resolved
        self._diagnostic(
            f"[{self._adapter_id}] the {scope} context of '{record.session_id}' exceeds what the "
            "runtime accepts for this event; it is not marked injected"
        )
        return (context if truncate else ""), False

    @staticmethod
    def _registration_failure(report: Mapping[str, Any] | None) -> str | None:
        """None when `start-session` answered `started`, else why the session is unregistered."""
        if report is None:
            return "start-session unavailable"
        if read_status(report) == _STARTED:
            return None
        return describe_report(report)

    def _run_safely(self, function: str, inquiry: Mapping[str, Any]) -> Mapping[str, Any] | None:
        """Run a harness function; a failure is a diagnostic, never a raw leak to the model."""
        try:
            return self._runner.run_function(function, inquiry)
        except Exception as failure:  # noqa: BLE001 - every failure must be contained
            self._diagnostic(f"[{self._adapter_id}] {function} failed: {failure}")
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
    "Adapter",
    "HARNESS_REJECTED",
    "HARNESS_RESULT",
    "PLUGIN_UPDATED",
    "HarnessRequest",
    "HostDecisions",
    "HostEvents",
    "HostProfile",
    "classify_calls",
]
