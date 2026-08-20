"""Function 1's result: the orchestrator's workflow instruction refs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stores.session_log_store.report import Report


@dataclass(frozen=True)
class WorkflowInstructionsReport(Report):
    """Report the orchestrator's instruction refs, absent on error outcomes."""

    instructions: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract report, adding `instructions` only where resolved."""
        rendered = super().to_dict()
        if self.instructions is not None:
            rendered["instructions"] = list(self.instructions)
        return rendered


__all__ = ["WorkflowInstructionsReport"]
