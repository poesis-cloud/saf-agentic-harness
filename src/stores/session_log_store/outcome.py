"""The function outcome carried by every report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from stores.session_log_store.error import Error


@dataclass(frozen=True)
class Outcome:
    """Carry one invocation's outcome status and optional error detail."""

    status: str
    error: Error | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `outcome` object, with `error` only when present."""
        rendered: dict[str, Any] = {"status": self.status}
        if self.error is not None:
            rendered["error"] = self.error.to_dict()
        return rendered

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Outcome":
        """Build an outcome from a contract `outcome` object."""
        error_data = data.get("error")
        return cls(
            status=data["status"],
            error=Error.from_dict(error_data) if error_data is not None else None,
        )


__all__ = ["Outcome"]
