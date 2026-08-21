"""The session's two boundary writes to its own log: functions 0 and 11."""

from __future__ import annotations

import re
from typing import Any, Mapping

from config.access_control_list import AccessControlList
from errors import HarnessError, InquiryError, StateError
from services.session_lifecycle.session_end_report import SessionEndReport
from services.session_lifecycle.session_ref import SessionRef
from services.session_lifecycle.session_start_report import SessionStartReport
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


def _require_agent(agent: str) -> None:
    """Reject function 0's inquiry when the required `agent` slug is absent."""
    if not isinstance(agent, str) or not _SLUG_PATTERN.match(agent):
        raise InquiryError(
            "invalid-inquiry",
            f"Framework agent '{agent}' is missing or not a safe slug.",
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


def _carries_ending_entry(log: Log) -> bool:
    """Tell whether the log carries function 11's ending entry (C8)."""
    return any(entry.report.context.function == _END_FUNCTION for entry in log.entries)


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


class SessionLifecycle:
    """Open and close one session's log — the harness's session-scoped boundary writes."""

    def __init__(
        self,
        session_log_store: SessionLogStore,
        access_control_list: AccessControlList,
        clock: Clock | None = None,
    ) -> None:
        """Create the service over its injected log store, access control list, and clock."""
        self._session_log_store = session_log_store
        self._acl = access_control_list
        self._clock = clock or Clock()

    def start_session(
        self,
        agent: str,
        session_id: str,
        parent_session_id: str | None = None,
    ) -> SessionStartReport:
        """Register the framework-agent session that just opened, creating its log."""
        _require_valid_inquiry(session_id, parent_session_id)
        _require_agent(agent)
        context = Context(
            function=_START_FUNCTION,
            session_id=session_id,
            parent_session_id=parent_session_id,
            workflow_instance_id=None,
        )
        log = self._find_session_log(session_id)
        if log is not None:
            return self._replay_registration(context, log, agent)
        if not self._admits_session(agent, parent_session_id):
            return SessionStartReport(context=context, outcome=Outcome(status="not-applicable"))
        return self._register_session(context, agent)

    def end_session(self, session_id: str) -> SessionEndReport:
        """Close the session's log with its final entry, idempotently (C8-exempt)."""
        _require_valid_inquiry(session_id, None)
        log = self._find_session_log(session_id)
        session_ref = _find_session_ref(log) if log is not None else None
        context = Context(
            function=_END_FUNCTION,
            session_id=session_id,
            parent_session_id=session_ref.get("parentSessionId") if session_ref else None,
            workflow_instance_id=None,
        )
        report = SessionEndReport(context=context, outcome=Outcome(status="ended"))
        if log is None or _carries_ending_entry(log):
            return report
        failure = self._append_entry(session_id, report)
        if failure is None:
            return report
        return SessionEndReport(context=context, outcome=_error_outcome(failure))

    def _find_session_log(self, session_id: str) -> Log | None:
        """Load the session's log, or none when the session was never registered."""
        try:
            return self._session_log_store.load_session_log(session_id)
        except StateError as error:
            if error.code != _SESSION_UNREGISTERED:
                raise
            return None

    def _replay_registration(
        self,
        context: Context,
        log: Log,
        agent: str,
    ) -> SessionStartReport:
        """Answer a re-delivered start against an already-registered session."""
        if _carries_ending_entry(log):
            return SessionStartReport(
                context=context,
                outcome=Outcome(
                    status="state-error",
                    error=Error(
                        code="session-ended",
                        message=f"Session '{context.session_id}' carries an ending entry.",
                        retryable=False,
                    ),
                ),
            )
        registered = _find_session_ref(log)
        if registered is not None and registered["agent"] != agent:
            report = SessionStartReport(
                context=context,
                outcome=Outcome(
                    status="state-error",
                    error=Error(
                        code="session-conflict",
                        message=(
                            f"Session '{context.session_id}' is registered to "
                            f"'{registered['agent']}', not '{agent}'."
                        ),
                        retryable=False,
                    ),
                ),
            )
            failure = self._append_entry(context.session_id, report)
            if failure is None:
                return report
            return SessionStartReport(context=context, outcome=_error_outcome(failure))
        return SessionStartReport(
            context=context,
            outcome=Outcome(status="started"),
            session=SessionRef(
                agent=agent,
                session_id=context.session_id,
                parent_session_id=(
                    registered["parentSessionId"] if registered else context.parent_session_id
                ),
            ),
        )

    def _admits_session(self, agent: str, parent_session_id: str | None) -> bool:
        """Tell whether persisted state names a target for this start (rule 2)."""
        if parent_session_id is None:
            return self._acl.is_framework_agent(agent)
        parent_log = self._find_session_log(parent_session_id)
        if parent_log is None:
            return False
        return _find_correlated_resolution(parent_log, agent) is not None

    def _register_session(self, context: Context, agent: str) -> SessionStartReport:
        """Write the registration entry as the new log's first line."""
        report = SessionStartReport(
            context=context,
            outcome=Outcome(status="started"),
            session=SessionRef(
                agent=agent,
                session_id=context.session_id,
                parent_session_id=context.parent_session_id,
            ),
        )
        try:
            self._session_log_store.create_session_log(
                LogEntry(timestamp=self._clock.read_timestamp(), report=report)
            )
        except HarnessError as error:
            return SessionStartReport(context=context, outcome=_error_outcome(error))
        return report

    def _append_entry(self, session_id: str, report: Report) -> HarnessError | None:
        """Journal one entry, answering the failure that lost it instead of raising."""
        try:
            self._session_log_store.append_log_entry(
                session_id, LogEntry(timestamp=self._clock.read_timestamp(), report=report)
            )
        except HarnessError as error:
            return error
        return None


__all__ = ["SessionLifecycle"]
