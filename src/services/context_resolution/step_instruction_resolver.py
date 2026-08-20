"""Function 6: which behavioral guidance this step's session loads."""

from __future__ import annotations

from typing import Any, ClassVar, Mapping

from services.context_resolution.context_resolver import ContextResolver
from services.context_resolution.step_instructions_report import StepInstructionsReport
from stores.session_log_store.context import Context
from stores.session_log_store.log_entry import LogEntry
from stores.session_log_store.outcome import Outcome


class StepInstructionResolver(ContextResolver):
    """Resolve the correlated step's declared instructions from configuration alone."""

    _FUNCTION: ClassVar[str] = "resolve-step-instructions"

    def resolve_step_instructions(
        self,
        session_id: str,
        parent_session_id: str | None = None,
    ) -> StepInstructionsReport:
        """Resolve the instruction refs injected at the step session's open."""
        return self._resolve_session_context(session_id, parent_session_id)

    def _correlate_step_resolution(self, session_ref: Mapping[str, Any]) -> LogEntry:
        """Require the unresolved step resolution this session acts (precondition E)."""
        return self._require_step_correlation(session_ref)

    def _resolve_refs(
        self,
        session_ref: Mapping[str, Any],
        resolution: LogEntry | None,
    ) -> tuple[str, ...]:
        """List the instruction refs the correlated step declares."""
        return self._find_configured_step(resolution).instructions

    def _build_report(
        self,
        context: Context,
        outcome: Outcome,
        refs: tuple[str, ...] | None,
    ) -> StepInstructionsReport:
        """Build function 6's report."""
        return StepInstructionsReport(context=context, outcome=outcome, instructions=refs)


__all__ = ["StepInstructionResolver"]
