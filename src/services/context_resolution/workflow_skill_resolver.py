"""Function 2: which skills the orchestrator's session loads."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from services.context_resolution.context_resolver import ContextResolver
from services.context_resolution.workflow_skills_report import WorkflowSkillsReport
from stores.session_log_store.context import Context
from stores.session_log_store.log_entry import LogEntry
from stores.session_log_store.outcome import Outcome


class WorkflowSkillResolver(ContextResolver):
    """Resolve the orchestrator's procedure skills from configuration alone."""

    _FUNCTION: ClassVar[str] = "resolve-workflow-skills"

    def resolve_workflow_skills(
        self,
        session_id: str,
        parent_session_id: str | None = None,
    ) -> WorkflowSkillsReport:
        """Resolve the skill ids loaded at the orchestrator's session open."""
        return self._resolve_session_context(session_id, parent_session_id)

    def _correlate_step_resolution(self, session_ref: Mapping[str, Any]) -> LogEntry | None:
        """Enforce the orchestrator session kind (function 1's precondition E)."""
        return self._refuse_step_session(session_ref)

    def _resolve_refs(
        self,
        session_ref: Mapping[str, Any],
        resolution: LogEntry | None,
    ) -> tuple[str, ...]:
        """List the skill ids the session's orchestrator declares."""
        return self._list_facilitated_skills(session_ref["agent"])

    def _build_report(
        self,
        context: Context,
        outcome: Outcome,
        refs: tuple[str, ...] | None,
    ) -> WorkflowSkillsReport:
        """Build function 2's report."""
        return WorkflowSkillsReport(context=context, outcome=outcome, skills=refs)


__all__ = ["WorkflowSkillResolver"]
