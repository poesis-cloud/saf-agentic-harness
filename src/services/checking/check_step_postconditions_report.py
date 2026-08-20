"""Function 10's result: the step-delivery evaluation's report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from services.checking.condition_check_report import ConditionCheckReport


@dataclass(frozen=True)
class CheckStepPostconditionsReport(ConditionCheckReport):
    """Report the aggregate postcondition outcome — the step outcome the cursor reads.

    Spec (function 10, invariant 3): this journaled outcome is exactly what
    function 3's cursor reads; a step whose latest outcome passes counts as executed.
    """

    CONTRACT_ID: ClassVar[str] = (
        "gsmarc://saf/contracts/api/check-step-postconditions.output/v1"
    )


__all__ = ["CheckStepPostconditionsReport"]
