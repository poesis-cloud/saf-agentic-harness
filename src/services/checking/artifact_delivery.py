"""Function 10's delivery verdict: did the step's session commit its artifact?"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ArtifactDelivery:
    """Carry the step's artifact kind, its delivered instance, and the verdict.

    Spec (function 10, invariant 5): delivery is an invariant, not a condition — it
    has no slug and is never configurable. `artifact_path` names the one delivered
    instance, or is `None` when the step delivered zero or more than one.
    """

    artifact: str
    artifact_path: str | None
    outcome: str
    failure_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `artifactDelivery` object."""
        rendered: dict[str, Any] = {
            "artifact": self.artifact,
            "artifactPath": self.artifact_path,
            "outcome": self.outcome,
        }
        if self.failure_message is not None:
            rendered["failureMessage"] = self.failure_message
        return rendered


__all__ = ["ArtifactDelivery"]
