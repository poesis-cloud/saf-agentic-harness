"""Function 6's result: the correlated step's declared instruction refs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stores.session_log_store.report import Report


@dataclass(frozen=True)
class StepInstructionsReport(Report):
    """Report the step's instruction refs, absent on error outcomes."""

    instructions: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract report, adding `instructions` only where resolved."""
        rendered = super().to_dict()
        if self.instructions is not None:
            rendered["instructions"] = list(self.instructions)
        return rendered


__all__ = ["StepInstructionsReport"]
