"""Function 5's result: the precondition gate's report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from services.checking.condition_check_report import ConditionCheckReport


@dataclass(frozen=True)
class CheckStepPreconditionsReport(ConditionCheckReport):
    """Report the aggregate precondition outcome and its per-condition checks.

    Spec (Classes): a leaf type whose identity is its contract's `$id`, not its
    shape — structurally identical to function 10's report, contractually distinct.
    """

    CONTRACT_ID: ClassVar[str] = (
        "gsmarc://saf/contracts/api/check-step-preconditions.output/v1"
    )


__all__ = ["CheckStepPreconditionsReport"]
