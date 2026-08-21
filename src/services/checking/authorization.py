"""Function 8's decision detail: who wrote what, and why it was refused."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Authorization:
    """Carry one write's authorization decision over its resolved resource.

    Spec (function 8, Classes): the `Authorization` carries `actor`,
    `artifactPath`, `action`, `resource`, and the failure message — the actor
    derived from the registered session (invariant 1) and the resource from the
    write path (invariant 2), never either from the inquiry.
    """

    actor: str
    artifact_path: str
    action: str
    resource: str
    failure_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `authorization` object with its camelCase keys.

        The `allowed` branch forbids `failureMessage`, so an allow renders none.
        """
        rendered: dict[str, Any] = {
            "actor": self.actor,
            "artifactPath": self.artifact_path,
            "action": self.action,
            "resource": self.resource,
        }
        if self.failure_message is not None:
            rendered["failureMessage"] = self.failure_message
        return rendered


__all__ = ["Authorization"]
