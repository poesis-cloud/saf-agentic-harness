"""The session mechanics every checking service shares: C8, journaling, the error seam."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable

from errors import HarnessError, StateError
from stores.session_log_store import (
    Context,
    Error,
    Log,
    LogEntry,
    Outcome,
    Report,
    SessionLogStore,
)

_END_SESSION_FUNCTION = "end-session"
_SESSION_ENDED_CODE = "session-ended"
_SESSION_UNREGISTERED_CODE = "session-unregistered"

# Rule 3 (the C8 refusal) and rule 4 (`session-unregistered` has no log to journal to).
_NON_JOURNALABLE_CODES = frozenset({_SESSION_ENDED_CODE, _SESSION_UNREGISTERED_CODE})

# Rule 1 assigns `session-unregistered` to `inquiry-error` — the mediated backstop —
# while the store raises it as a state failure, so the seam re-assigns it by code.
_STATUS_BY_CODE = {_SESSION_UNREGISTERED_CODE: "inquiry-error"}


def _utc_now() -> str:
    """Read the wall-clock write time of a log entry."""
    return datetime.now(timezone.utc).isoformat()


class CheckingService(ABC):
    """Run one session-bound checking function behind the shared outcome rules.

    Spec (Classes): an exception is raised where the failure is detected and caught
    at exactly ONE seam — the service's public method, which renders the matching
    error report and journals it per the failure-journaling rules.
    """

    def __init__(
        self,
        session_log_store: SessionLogStore,
        clock: Callable[[], str] | None = None,
    ) -> None:
        """Create the service over its log store and its wall clock."""
        self._session_log_store = session_log_store
        self._clock = clock or _utc_now

    @abstractmethod
    def _build_report(
        self,
        session_id: str,
        parent_session_id: str | None,
        workflow_instance_id: str | None,
        outcome: Outcome,
    ) -> Report:
        """Build this function's own report type carrying only the envelope."""

    def _execute_check(
        self, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Run the function behind the C8 refusal and the single error seam."""
        workflow_instance_id: str | None = None
        try:
            log = self._session_log_store.load_session_log(session_id)
            self._refuse_ended_session(log)
            workflow_instance_id = self._find_workflow_instance_id(log)
            return self._check_open_session(
                session_id, parent_session_id, log, workflow_instance_id
            )
        except HarnessError as error:
            report = self._build_report(
                session_id,
                parent_session_id,
                workflow_instance_id,
                Outcome(
                    status=_STATUS_BY_CODE.get(error.code, error.status),
                    error=Error(
                        code=error.code,
                        message=error.message,
                        retryable=error.retryable,
                    ),
                ),
            )
            if error.code not in _NON_JOURNALABLE_CODES:
                self._journal_report(session_id, report, best_effort=True)
            return report

    @abstractmethod
    def _check_open_session(
        self,
        session_id: str,
        parent_session_id: str | None,
        log: Log,
        workflow_instance_id: str | None,
    ) -> Report:
        """Run this function's own logic against an open, registered session."""

    def _refuse_ended_session(self, log: Log) -> None:
        """Refuse any call against a session whose log carries an ending entry.

        C8 / rule 3: the refusal is `state-error` with code `session-ended`, never
        journaled — no entry ever follows the ending entry.
        """
        if any(
            entry.report.context.function == _END_SESSION_FUNCTION
            for entry in log.entries
        ):
            raise StateError(
                _SESSION_ENDED_CODE,
                f"Session '{log.session_id}' has ended and accepts no further "
                f"invocation.",
                False,
            )

    def _find_workflow_instance_id(self, log: Log) -> str | None:
        """Read the workflow instance the session's latest correlated entry names."""
        for entry in reversed(log.entries):
            if entry.report.context.workflow_instance_id is not None:
                return entry.report.context.workflow_instance_id
        return None

    def _build_context(
        self,
        function: str,
        session_id: str,
        parent_session_id: str | None,
        workflow_instance_id: str | None,
    ) -> Context:
        """Build the replay and correlation context of one invocation's report."""
        return Context(
            function=function,
            session_id=session_id,
            parent_session_id=parent_session_id,
            workflow_instance_id=workflow_instance_id,
        )

    def _journal_report(
        self, session_id: str, report: Report, *, best_effort: bool = False
    ) -> None:
        """Append this invocation's entry — 1 invocation = 1 entry.

        Rule 1: a failing append loses the entry but never the report, so the error
        seam's own journaling is best-effort and cannot recurse.
        """
        entry = LogEntry(timestamp=self._clock(), report=report)
        try:
            self._session_log_store.append_log_entry(session_id, entry)
        except HarnessError:
            if not best_effort:
                raise


__all__ = ["CheckingService"]
