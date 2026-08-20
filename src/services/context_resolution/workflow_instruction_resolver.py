"""Function 1: which workflow-context guidance the orchestrator's session loads."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from services.context_resolution.context_resolver import ContextResolver
from services.context_resolution.workflow_instructions_report import (
    WorkflowInstructionsReport,
)
from stores.session_log_store.context import Context
from stores.session_log_store.log_entry import LogEntry
from stores.session_log_store.outcome import Outcome


class WorkflowInstructionResolver(ContextResolver):
    """Resolve the orchestrator's workflow instructions from configuration alone."""

    _FUNCTION: ClassVar[str] = "resolve-workflow-instructions"

    def resolve_workflow_instructions(
        self,
        session_id: str,
        parent_session_id: str | None = None,
    ) -> WorkflowInstructionsReport:
        """Resolve the instruction refs injected at the orchestrator's session open."""
        return self._resolve_session_context(session_id, parent_session_id)

    def _correlate_step_resolution(self, session_ref: Mapping[str, Any]) -> LogEntry | None:
        """Enforce the orchestrator session kind (precondition E)."""
        return self._refuse_step_session(session_ref)

    def _resolve_refs(
        self,
        session_ref: Mapping[str, Any],
        resolution: LogEntry | None,
    ) -> tuple[str, ...]:
        """List the instruction refs the session's orchestrator declares."""
        return self._list_facilitated_instructions(session_ref["agent"])

    def _build_report(
        self,
        context: Context,
        outcome: Outcome,
        refs: tuple[str, ...] | None,
    ) -> WorkflowInstructionsReport:
        """Build function 1's report."""
        return WorkflowInstructionsReport(context=context, outcome=outcome, instructions=refs)


__all__ = ["WorkflowInstructionResolver"]
