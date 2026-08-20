"""The opened session's own metadata, as function 0 records it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SessionRef:
    """Carry one registered session's framework-agent identity and ids."""

    agent: str
    session_id: str
    parent_session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `session` object with its camelCase keys."""
        return {
            "agent": self.agent,
            "sessionId": self.session_id,
            "parentSessionId": self.parent_session_id,
        }


__all__ = ["SessionRef"]
