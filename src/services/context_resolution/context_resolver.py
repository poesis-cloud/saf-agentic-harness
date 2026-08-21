"""The shared correlation, seam, and journaling of the four context resolvers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from itertools import chain
from typing import Any, ClassVar, Iterable, Mapping

from config.step import Step
from config.workflow_catalog import WorkflowCatalog
from errors import ConfigurationError, HarnessError, InquiryError, StateError
from stores.session_log_store.context import Context
from stores.session_log_store.error import Error
from stores.session_log_store.log import Log
from stores.session_log_store.log_entry import LogEntry
from stores.session_log_store.outcome import Outcome
from stores.session_log_store.report import Report
from stores.session_log_store.session_log_store import SessionLogStore
from utils.clock import Clock

_START_FUNCTION = "start-session"
_END_FUNCTION = "end-session"
_RESOLVE_STEP_FUNCTION = "resolve-step"
_STEP_RESOLUTION_STATUS = "step-resolution"
_POSTCONDITIONS_FUNCTION = "check-step-postconditions"
_SESSION_UNREGISTERED = "session-unregistered"
_UNJOURNALED_CODES = frozenset({"session-ended", _SESSION_UNREGISTERED})
_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


def _require_valid_inquiry(session_id: str, parent_session_id: str | None) -> None:
    """Reject an inquiry no contract-valid report could be built for (rule 4)."""
    if not isinstance(session_id, str) or not _SLUG_PATTERN.match(session_id):
        raise InquiryError(
            "invalid-inquiry",
            f"Session id '{session_id}' is not a safe slug.",
            False,
        )
    if parent_session_id is not None and not _SLUG_PATTERN.match(parent_session_id):
        raise InquiryError(
            "invalid-inquiry",
            f"Parent session id '{parent_session_id}' is not a safe slug.",
            False,
        )


def _error_outcome(error: HarnessError) -> Outcome:
    """Render an outcome from the exception the failure was detected with."""
    return Outcome(
        status=error.status,
        error=Error(code=error.code, message=error.message, retryable=error.retryable),
    )


def _find_session_ref(log: Log) -> Mapping[str, Any] | None:
    """Find the registration entry's `session` object, if the log carries one."""
    for entry in log.entries:
        if entry.report.context.function == _START_FUNCTION:
            session = entry.report.payload.get("session")
            if session is not None:
                return session
    return None


def _refuse_ended_session(log: Log, session_id: str) -> None:
    """Refuse any call against a session whose log carries an ending entry (C8)."""
    if any(entry.report.context.function == _END_FUNCTION for entry in log.entries):
        raise StateError(
            "session-ended",
            f"Session '{session_id}' carries an ending entry.",
            False,
        )


def _find_correlated_resolution(log: Log, actor: str) -> LogEntry | None:
    """Find the log's latest step resolution for this actor with no later outcome."""
    correlated: LogEntry | None = None
    for entry in log.entries:
        function = entry.report.context.function
        if function == _RESOLVE_STEP_FUNCTION and (
            entry.report.outcome.status == _STEP_RESOLUTION_STATUS
        ):
            step = entry.report.payload.get("step", {})
            correlated = entry if step.get("actor") == actor else None
        elif function == _POSTCONDITIONS_FUNCTION:
            correlated = None
    return correlated


def _dedupe_refs(refs: Iterable[str]) -> tuple[str, ...]:
    """Collapse repeated refs, keeping first-seen configuration order."""
    ordered: dict[str, None] = {}
    for ref in refs:
        ordered.setdefault(ref, None)
    return tuple(ordered)


class ContextResolver(ABC):
    """Resolve one session's injected context from configuration and journal it.

    Spec (functions 1–2, 6–7): the four resolvers share one shape — correlate the
    session to its context source, look the refs up in configuration, and append the
    invocation's own entry. Only the correlation rule, the lookup, and the report type
    differ, so each concrete resolver supplies exactly those three.
    """

    _FUNCTION: ClassVar[str]

    def __init__(
        self,
        workflow_catalog: WorkflowCatalog,
        session_log_store: SessionLogStore,
        clock: Clock | None = None,
    ) -> None:
        """Create the resolver over its injected catalog, log store, and entry clock."""
        self._catalog = workflow_catalog
        self._session_log_store = session_log_store
        self._clock = clock or Clock()

    @abstractmethod
    def _correlate_step_resolution(self, session_ref: Mapping[str, Any]) -> LogEntry | None:
        """Enforce the resolver's session kind, answering the correlated resolution."""

    @abstractmethod
    def _resolve_refs(
        self,
        session_ref: Mapping[str, Any],
        resolution: LogEntry | None,
    ) -> tuple[str, ...]:
        """Look the session's injected refs up in configuration."""

    @abstractmethod
    def _build_report(
        self,
        context: Context,
        outcome: Outcome,
        refs: tuple[str, ...] | None,
    ) -> Report:
        """Build this resolver's own concrete report type."""

    def _resolve_session_context(
        self,
        session_id: str,
        parent_session_id: str | None,
    ) -> Report:
        """Run the shared resolution seam: correlate, look up, journal, report."""
        _require_valid_inquiry(session_id, parent_session_id)
        workflow_instance_id: str | None = None
        refs: tuple[str, ...] | None = None
        try:
            session_ref = self._load_session_ref(session_id)
            resolution = self._correlate_step_resolution(session_ref)
            if resolution is not None:
                workflow_instance_id = resolution.report.context.workflow_instance_id
            refs = self._resolve_refs(session_ref, resolution)
            outcome = Outcome(status="resolved")
        except HarnessError as error:
            outcome = _error_outcome(error)
        context = Context(
            function=self._FUNCTION,
            session_id=session_id,
            parent_session_id=parent_session_id,
            workflow_instance_id=workflow_instance_id,
        )
        report = self._build_report(context, outcome, refs)
        if outcome.error is not None and outcome.error.code in _UNJOURNALED_CODES:
            return report
        failure = self._append_entry(session_id, report)
        if failure is None:
            return report
        return self._build_report(context, _error_outcome(failure), None)

    def _load_session_ref(self, session_id: str) -> Mapping[str, Any]:
        """Load the session's registration, refusing an ended or unregistered session."""
        try:
            log = self._session_log_store.load_session_log(session_id)
        except StateError as error:
            if error.code != _SESSION_UNREGISTERED:
                raise
            raise InquiryError(_SESSION_UNREGISTERED, error.message, False) from error
        _refuse_ended_session(log, session_id)
        session_ref = _find_session_ref(log)
        if session_ref is None:
            raise InquiryError(
                _SESSION_UNREGISTERED,
                f"Session '{session_id}' carries no registration entry.",
                False,
            )
        return session_ref

    def _refuse_step_session(self, session_ref: Mapping[str, Any]) -> None:
        """Refuse a session an unresolved step resolution correlates to (functions 1–2)."""
        if self._find_parent_correlation(session_ref) is not None:
            raise StateError(
                "session-kind-mismatch",
                (
                    f"Session '{session_ref['sessionId']}' is a step session and loads "
                    "the step context functions instead."
                ),
                False,
            )
        return None

    def _require_step_correlation(self, session_ref: Mapping[str, Any]) -> LogEntry:
        """Require the unresolved step resolution this session acts (functions 6–7)."""
        correlation = self._find_parent_correlation(session_ref)
        if correlation is None:
            raise StateError(
                "step-correlation-missing",
                (
                    f"No unresolved step resolution correlates to session "
                    f"'{session_ref['sessionId']}'."
                ),
                False,
            )
        return correlation

    def _find_configured_step(self, resolution: LogEntry) -> Step:
        """Look the correlated step up in the workflow configuration."""
        instance_id = resolution.report.context.workflow_instance_id
        workflow = self._catalog.find_workflow(instance_id.rsplit("-", 1)[0])
        step_slug = resolution.report.payload["step"]["slug"]
        for step in workflow.steps:
            if step.slug == step_slug:
                return step
        raise ConfigurationError(
            "unknown-step",
            f"Workflow '{workflow.slug}' declares no step '{step_slug}'.",
            False,
        )

    def _list_facilitated_instructions(self, agent: str) -> tuple[str, ...]:
        """List the instruction refs of every workflow this orchestrator facilitates."""
        return _dedupe_refs(
            chain.from_iterable(
                workflow.instructions
                for workflow in self._catalog.list_facilitated_workflows(agent)
            )
        )

    def _list_facilitated_skills(self, agent: str) -> tuple[str, ...]:
        """List the skill ids of every workflow this orchestrator facilitates."""
        return _dedupe_refs(
            chain.from_iterable(
                workflow.skills
                for workflow in self._catalog.list_facilitated_workflows(agent)
            )
        )

    def _find_parent_correlation(self, session_ref: Mapping[str, Any]) -> LogEntry | None:
        """Match the session to its parent's latest unresolved step resolution."""
        parent_session_id = session_ref.get("parentSessionId")
        if parent_session_id is None:
            return None
        try:
            parent_log = self._session_log_store.load_session_log(parent_session_id)
        except StateError as error:
            if error.code != _SESSION_UNREGISTERED:
                raise
            return None
        correlated = _find_correlated_resolution(parent_log, session_ref["agent"])
        if correlated is None or correlated.report.context.workflow_instance_id is None:
            return None
        return correlated

    def _append_entry(self, session_id: str, report: Report) -> HarnessError | None:
        """Journal one entry, answering the failure that lost it instead of raising."""
        try:
            self._session_log_store.append_log_entry(
                session_id, LogEntry(timestamp=self._clock.read_timestamp(), report=report)
            )
        except HarnessError as error:
            return error
        return None


__all__ = ["ContextResolver"]
