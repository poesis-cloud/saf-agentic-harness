"""The resolution core: which step of a workflow instance comes next (function 3)."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Sequence

from config import Step, StepCondition, Workflow, WorkflowCatalog
from errors import HarnessError, InquiryError, StateError, SystemFailureError
from services.step_resolution.step_resolution_report import StepResolutionReport
from stores.session_log_store import (
    Context,
    Error,
    Log,
    LogEntry,
    Outcome,
    SessionLogStore,
    WorkflowInstanceView,
)
from utils.clock import Clock

_FUNCTION = "resolve-step"
_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")
_PRECONDITION_KIND = "precondition"
_START_FUNCTION = "start-session"
_END_FUNCTION = "end-session"
_ENDED_STATUS = "ended"
_STEP_RESOLUTION_STATUS = "step-resolution"
# The generic recovery invocation, when the composition root names no installed one.
_GENERIC_END_SESSION_INVOCATION = ("python", "harness.py")
_POSTCONDITIONS_FUNCTION = "check-step-postconditions"


def _require_inquiry_slugs(session_id: str, parent_session_id: str | None) -> None:
    """Reject session attribution the contract cannot admit — a session id becomes a filename."""
    for value in (session_id, parent_session_id):
        if value is None:
            continue
        if not isinstance(value, str) or _SLUG_PATTERN.match(value) is None:
            raise InquiryError(
                "invalid-inquiry",
                f"Session attribution '{value}' is not a safe slug.",
                False,
            )


def _is_step_resolution(entry: LogEntry) -> bool:
    """Tell whether the entry journals a step resolution."""
    return (
        entry.report.context.function == _FUNCTION
        and entry.report.outcome.status == _STEP_RESOLUTION_STATUS
    )


def _has_ending_entry(log: Log) -> bool:
    """Tell whether the session's log carries function 11's ending entry (C8)."""
    return any(
        entry.report.context.function == _END_FUNCTION
        and entry.report.outcome.status == _ENDED_STATUS
        for entry in log.entries
    )


def _find_bound_instance(log: Log) -> str | None:
    """Find the workflow instance this session already drives, if any (invariant 9)."""
    for entry in reversed(log.entries):
        instance_id = entry.report.context.workflow_instance_id
        if instance_id is not None:
            return instance_id
    return None


def _find_session_agent(log: Log) -> str | None:
    """Read the session's framework-agent identity from its start entry."""
    for entry in log.entries:
        if entry.report.context.function == _START_FUNCTION:
            session = entry.report.payload.get("session", {})
            return session.get("agent")
    return None


def _find_unfinished_resolution(view: WorkflowInstanceView) -> LogEntry | None:
    """Find the instance's unfinished step resolution, if any.

    An unfinished step resolution is a `step-resolution` entry with no later
    function-10 outcome in the instance view's timestamp order (function 3,
    invariant 10) — latest wins, so at most one is unfinished.
    """
    pending: LogEntry | None = None
    for entry in view.entries:
        if _is_step_resolution(entry):
            pending = entry
        elif entry.report.context.function == _POSTCONDITIONS_FUNCTION:
            pending = None
    return pending


def _find_eligible_step(workflow: Workflow, executed_steps: frozenset[str]) -> Step | None:
    """Find the first remaining step whose precondition predecessors are all executed."""
    for step in workflow.steps:
        if step.slug in executed_steps:
            continue
        predecessors = tuple(
            condition.step
            for condition in step.conditions
            if isinstance(condition, StepCondition) and condition.kind == _PRECONDITION_KIND
        )
        if all(predecessor in executed_steps for predecessor in predecessors):
            return step
    return None


def _render_error_report(context: Context, failure: HarnessError) -> StepResolutionReport:
    """Render one failure as its contract report — status from the exception type."""
    return StepResolutionReport(
        context=context,
        outcome=Outcome(
            status=failure.status,
            error=Error(code=failure.code, message=failure.message, retryable=failure.retryable),
        ),
    )


class StepResolver:
    """Resolve the next step of a workflow instance from the journaled outcomes alone.

    Spec (function 3): the harness alone governs sequencing — no agent selects steps, no
    instance id is ever accepted, and resolution writes nothing beyond its own log entry.
    """

    def __init__(
        self,
        session_log_store: SessionLogStore,
        workflow_catalog: WorkflowCatalog,
        clock: Clock | None = None,
        harness_invocation: Sequence[str] | None = None,
        framework_dir: str | None = None,
    ) -> None:
        """Create the resolver over its injected store, catalog, and entry clock.

        `harness_invocation` (the installed interpreter and `harness.py`) and
        `framework_dir` only shape the advisory recovery command a
        `workflow-instance-busy` message gives; without them it is the generic form.
        """
        self._session_log_store = session_log_store
        self._catalog = workflow_catalog
        self._clock = clock or Clock()
        self._harness_invocation = tuple(harness_invocation or _GENERIC_END_SESSION_INVOCATION)
        self._framework_dir = framework_dir

    def resolve_step(
        self,
        session_id: str,
        parent_session_id: str | None,
        workflow_slug: str,
    ) -> StepResolutionReport:
        """Resolve the workflow's next step for one session-attributed invocation."""
        _require_inquiry_slugs(session_id, parent_session_id)
        context = Context(
            function=_FUNCTION,
            session_id=session_id,
            parent_session_id=parent_session_id,
        )

        try:
            log = self._load_open_session_log(session_id)
        except HarnessError as failure:
            return _render_error_report(context, failure)

        try:
            workflow = self._find_facilitated_workflow(log, workflow_slug)
            instance_id = self._deduce_workflow_instance(log, workflow)
            view = self._session_log_store.load_workflow_instance_view(instance_id)
            in_flight = self._require_single_driver(view, session_id)
        except HarnessError as failure:
            return self._journal_report(_render_error_report(context, failure))

        try:
            context = Context(
                function=_FUNCTION,
                session_id=session_id,
                parent_session_id=parent_session_id,
                workflow_instance_id=instance_id,
            )
            self._require_no_step_in_flight(view, in_flight)
            step = _find_eligible_step(workflow, view.list_executed_steps())
        except HarnessError as failure:
            return self._journal_report(_render_error_report(context, failure))

        status = _STEP_RESOLUTION_STATUS if step is not None else "no-next-step"
        return self._journal_report(
            StepResolutionReport(context=context, outcome=Outcome(status=status), step=step)
        )

    def _load_open_session_log(self, session_id: str) -> Log:
        """Load the attributed session's log, refusing an unregistered or ended session."""
        try:
            log = self._session_log_store.load_session_log(session_id)
        except StateError as failure:  # the store's only failure here is a missing log
            raise InquiryError("session-unregistered", failure.message, False) from failure
        if _has_ending_entry(log):
            raise StateError(
                "session-ended",
                f"Session '{session_id}' carries an ending entry and accepts no invocation.",
                False,
            )
        return log

    def _find_facilitated_workflow(self, log: Log, workflow_slug: str) -> Workflow:
        """Resolve the named workflow and assert the session's agent facilitates it."""
        workflow = self._catalog.workflows.get(workflow_slug)
        if workflow is None:
            raise InquiryError(
                "unknown-workflow",
                f"The catalog names no workflow '{workflow_slug}'.",
                False,
            )
        agent = _find_session_agent(log)
        if agent != workflow.facilitator:
            raise InquiryError(
                "not-facilitator",
                f"Agent '{agent}' does not facilitate the workflow '{workflow_slug}'.",
                False,
            )
        return workflow

    def _deduce_workflow_instance(self, log: Log, workflow: Workflow) -> str:
        """Deduce the instance: the one this session drives, else the latest open, else a new one.

        Invariant 9: a session drives at most one instance over its lifetime, so a
        session already bound to another workflow's instance is refused.
        """
        bound = _find_bound_instance(log)
        if bound is not None:
            if SessionLogStore.is_instance_of(bound, workflow.slug):
                return bound
            raise StateError(
                "session-instance-bound",
                f"Session '{log.session_id}' already drives the workflow instance "
                f"'{bound}' and cannot drive the workflow '{workflow.slug}'.",
                False,
            )
        latest_open = self._session_log_store.find_latest_open_instance(
            workflow.slug,
            workflow_steps=[step.slug for step in workflow.steps],
        )
        return latest_open or self._session_log_store.mint_workflow_instance_id(workflow.slug)

    def _require_single_driver(
        self, view: WorkflowInstanceView, session_id: str
    ) -> LogEntry | None:
        """Refuse while another live orchestrator session drives the workflow instance.

        Invariant 10: a call is refused (`workflow-instance-busy`) while another
        orchestrator session that has not ended (C8) holds an unfinished step
        resolution in the deduced workflow instance — journaled, like
        `session-instance-bound`, with no `workflowInstanceId`. Answers this
        session's own unfinished step resolution, if any.
        """
        pending = _find_unfinished_resolution(view)
        if pending is None:
            return None
        holder = pending.report.context.session_id
        if holder == session_id:
            return pending
        try:
            holder_log = self._session_log_store.load_session_log(holder)
        except StateError:
            return None
        if _has_ending_entry(holder_log):
            return None
        raise StateError(
            "workflow-instance-busy",
            f"Orchestrator session '{holder}' has not ended and holds the unfinished "
            f"step resolution of step '{pending.report.payload['step']['slug']}' in "
            f"workflow instance '{view.workflow_instance_id}'. If that session is really "
            f"gone (its host run was interrupted, so its end was never observed), end it "
            f"from your own terminal — only then — and retry: "
            f"{self._render_end_session_command(holder)}",
            False,
        )

    def _render_end_session_command(self, holder: str) -> str:
        """Render the advisory recovery command that ends the holding session (C8).

        Quoted for the platform's shell; the framework root it must run against is
        named beside it, since `harness.py` reads it from `FRAMEWORK_DIR`.
        """
        argv = [*self._harness_invocation, _END_FUNCTION, "--session-id", holder]
        quoted = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
        command = f"run: {quoted}"
        if self._framework_dir is not None:
            command += f" (with FRAMEWORK_DIR={self._framework_dir})"
        return command

    def _require_no_step_in_flight(
        self, view: WorkflowInstanceView, in_flight: LogEntry | None
    ) -> None:
        """Refuse a call arriving between a step's resolution and its journaled outcome."""
        if in_flight is not None:
            raise StateError(
                "step-in-flight",
                f"Step '{in_flight.report.payload['step']['slug']}' is in flight in instance "
                f"'{view.workflow_instance_id}'.",
                False,
            )

    def _journal_report(self, report: StepResolutionReport) -> StepResolutionReport:
        """Append this invocation's single entry; a failing append surfaces `system-error`
        on a success report, while an error report keeps its primary error.

        Spec (rule 4): a completed invocation whose log append fails "still returns its
        report" — the entry is lost, never the step the caller was promised.
        """
        entry = LogEntry(timestamp=self._clock.read_timestamp(), report=report)
        try:
            self._session_log_store.append_log_entry(report.context.session_id, entry)
        except (OSError, HarnessError) as failure:
            if report.outcome.error is not None:
                # An error report whose own entry is lost keeps its primary error (rule 1).
                return report
            lost = SystemFailureError("log-append-failed", str(failure), True)
            return StepResolutionReport(
                context=report.context,
                outcome=Outcome(
                    status=lost.status,
                    error=Error(code=lost.code, message=lost.message, retryable=lost.retryable),
                ),
                step=report.step,
            )
        return report


__all__ = ["StepResolver"]
