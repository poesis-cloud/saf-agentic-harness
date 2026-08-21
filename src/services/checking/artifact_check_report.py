"""Function 9's result: the commit gate's report over one staged write set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from services.checking.artifact_check import ArtifactCheck
from stores.session_log_store import Report

_REVERTED = "reverted"


@dataclass(frozen=True)
class ArtifactCheckReport(Report):
    """Report the whole set's verdict plus, when reverted, its failing paths.

    Spec (Classes): an empty `artifact_checks` tuple renders as the property's
    ABSENCE in the `valid` contract branch.
    """

    CONTRACT_ID: ClassVar[str] = (
        "gsmarc://saf/contracts/api/check-step-artifact.output/v1"
    )

    artifact_checks: tuple[ArtifactCheck, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Render the envelope, adding `artifactChecks` on the reverted branch only."""
        rendered = super().to_dict()
        if self.outcome.status == _REVERTED:
            rendered["artifactChecks"] = [
                check.to_dict() for check in self.artifact_checks
            ]
        return rendered


__all__ = ["ArtifactCheckReport"]
