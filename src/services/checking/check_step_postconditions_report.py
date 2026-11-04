"""Function 10's result: the step-delivery evaluation's report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from services.checking.artifact_delivery import ArtifactDelivery
from services.checking.condition_check_report import ConditionCheckReport

_CONDITION_STATUSES = frozenset({"pass", "fail"})


@dataclass(frozen=True)
class CheckStepPostconditionsReport(ConditionCheckReport):
    """Report the aggregate step outcome — the step outcome the cursor reads.

    Spec (function 10, invariants 3-5): this journaled outcome is exactly what
    function 3's cursor reads; it passes only if the step delivered its artifact
    (`artifactDelivery`) and every declared postcondition passes.
    """

    CONTRACT_ID: ClassVar[str] = (
        "gsmarc://saf/contracts/api/check-step-postconditions.output/v1"
    )

    artifact_delivery: ArtifactDelivery | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the condition report plus `artifactDelivery` on the checking branches."""
        rendered = super().to_dict()
        if self.outcome.status in _CONDITION_STATUSES and self.artifact_delivery:
            rendered["artifactDelivery"] = self.artifact_delivery.to_dict()
        return rendered


__all__ = ["CheckStepPostconditionsReport"]
