"""Function 7: which skills this step's session loads."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from services.context_resolution.context_resolver import ContextResolver
from services.context_resolution.step_skills_report import StepSkillsReport
from stores.session_log_store.context import Context
from stores.session_log_store.log_entry import LogEntry
from stores.session_log_store.outcome import Outcome


class StepSkillResolver(ContextResolver):
    """Resolve the correlated step's declared skills from configuration alone."""

    _FUNCTION: ClassVar[str] = "resolve-step-skills"

    def resolve_step_skills(
        self,
        session_id: str,
        parent_session_id: str | None = None,
    ) -> StepSkillsReport:
        """Resolve the skill ids loaded at the step session's open."""
        return self._resolve_session_context(session_id, parent_session_id)

    def _correlate_step_resolution(self, session_ref: Mapping[str, Any]) -> LogEntry:
        """Require the unresolved step resolution this session acts (function 6's E)."""
        return self._require_step_correlation(session_ref)

    def _resolve_refs(
        self,
        session_ref: Mapping[str, Any],
        resolution: LogEntry | None,
    ) -> tuple[str, ...]:
        """List the skill ids the correlated step declares."""
        return self._find_configured_step(resolution).skills

    def _build_report(
        self,
        context: Context,
        outcome: Outcome,
        refs: tuple[str, ...] | None,
    ) -> StepSkillsReport:
        """Build function 7's report."""
        return StepSkillsReport(context=context, outcome=outcome, skills=refs)


__all__ = ["StepSkillResolver"]
