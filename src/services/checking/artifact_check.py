"""Function 9's per-path record: why a staged path failed, and how it was discarded."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArtifactCheck:
    """Carry one FAILING staged path's failure message and its revert action.

    Spec (function 9, Interface): one record per failing path — valid siblings
    are discarded with the set, implied by set membership, never recorded here.
    """

    artifact_path: str
    failure_message: str
    revert_action: str
    revert_from: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `artifactChecks` item with its nested revert record."""
        revert: dict[str, Any] = {"action": self.revert_action}
        if self.revert_from is not None:
            revert["from"] = self.revert_from
        return {
            "artifactPath": self.artifact_path,
            "failureMessage": self.failure_message,
            "revert": revert,
        }


__all__ = ["ArtifactCheck"]
