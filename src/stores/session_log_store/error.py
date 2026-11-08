"""The contract error detail carried by error outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Error:
    """Carry one outcome's contract error detail."""

    code: str
    message: str
    retryable: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `error` object, omitting an unset `retryable`."""
        rendered: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.retryable is not None:
            rendered["retryable"] = self.retryable
        return rendered

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Error":
        """Build an error detail from a contract `error` object."""
        return cls(
            code=data["code"],
            message=data["message"],
            retryable=data.get("retryable"),
        )


__all__ = ["Error"]
