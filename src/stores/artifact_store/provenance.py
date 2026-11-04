"""The harness-owned provenance of one committed artifact instance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class Provenance:
    """Name the session whose harness commit last wrote one artifact instance.

    Spec (Terminology, "Artifact provenance"): derived by the harness from its own
    commit attribution (function 9 invariant 3), never read from the artifact's
    bytes; `session_id` is `None` when the commit carries no harness attribution.
    """

    PROPERTY: ClassVar[str] = "provenance"

    session_id: str | None = None

    def to_cel_data(self) -> dict[str, Any]:
        """Render the CEL-visible `provenance` object with its camelCase key."""
        return {"sessionId": self.session_id}


__all__ = ["Provenance"]
