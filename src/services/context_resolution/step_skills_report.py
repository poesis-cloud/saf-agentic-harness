"""Function 7's result: the correlated step's declared skill ids."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from stores.session_log_store.report import Report


@dataclass(frozen=True)
class StepSkillsReport(Report):
    """Report the step's skill ids, absent on error outcomes."""

    skills: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract report, adding `skills` only where resolved."""
        rendered = super().to_dict()
        if self.skills is not None:
            rendered["skills"] = list(self.skills)
        return rendered


__all__ = ["StepSkillsReport"]
