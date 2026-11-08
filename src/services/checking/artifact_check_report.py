"""Function 9's result: the commit gate's report over one staged write set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from services.checking.artifact_check import ArtifactCheck
from stores.artifact_store import Commit
from stores.session_log_store import Report

_VALID = "valid"
_REVERTED = "reverted"


@dataclass(frozen=True)
class ArtifactCheckReport(Report):
    """Report the whole set's verdict plus its commit record or its failing paths.

    Spec (Classes): an empty `artifact_checks` tuple renders as the property's
    ABSENCE in the `valid` contract branch. Spec (function 9, invariants 3-4):
    `valid` always records its commit — `commitId: null` with no
    `committedPaths` when nothing was committed.
    """

    CONTRACT_ID: ClassVar[str] = (
        "gsmarc://saf/contracts/api/check-step-artifact.output/v1"
    )

    artifact_checks: tuple[ArtifactCheck, ...] = ()
    commit: Commit | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the envelope plus the commit record (valid) or failing paths (reverted)."""
        rendered = super().to_dict()
        if self.outcome.status == _VALID:
            rendered["commitId"] = self.commit.commit_id if self.commit else None
            rendered["committedPaths"] = list(self.commit.paths) if self.commit else []
        if self.outcome.status == _REVERTED:
            rendered["artifactChecks"] = [
                check.to_dict() for check in self.artifact_checks
            ]
        return rendered


__all__ = ["ArtifactCheckReport"]
