"""The replay and correlation context carried by every function report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Context:
    """Identify the function, session, and workflow instance of one report."""

    function: str
    session_id: str
    parent_session_id: str | None = None
    workflow_instance_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `context` object with its camelCase keys."""
        return {
            "function": self.function,
            "sessionId": self.session_id,
            "parentSessionId": self.parent_session_id,
            "workflowInstanceId": self.workflow_instance_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Context":
        """Build a context from a contract `context` object."""
        return cls(
            function=data["function"],
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
            workflow_instance_id=data.get("workflowInstanceId"),
        )


__all__ = ["Context"]
