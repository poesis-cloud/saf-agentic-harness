"""The abstract condition-check report shared by functions 5 and 10."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import Any, ClassVar

from services.checking.condition_check import ConditionCheck
from stores.session_log_store import Report

_CONDITION_STATUSES = frozenset({"pass", "fail"})


@dataclass(frozen=True)
class ConditionCheckReport(Report, ABC):
    """Carry the per-condition checks beside the report envelope's aggregate outcome.

    Spec (Classes): functions 5 and 10 return structurally identical payloads bound
    to DISTINCT output contracts, so each is its own leaf type over this base; which
    function produced a report is read from `context.function`, never from the type.
    """

    CONTRACT_ID: ClassVar[str]

    condition_checks: tuple[ConditionCheck, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Render the envelope plus `conditionChecks`, on the checking branches only.

        The `not-applicable` and error branches of both output contracts declare no
        `conditionChecks`, and the contracts forbid unevaluated properties.
        """
        rendered = super().to_dict()
        if self.outcome.status in _CONDITION_STATUSES:
            rendered["conditionChecks"] = [
                check.to_dict() for check in self.condition_checks
            ]
        return rendered


__all__ = ["ConditionCheckReport"]
