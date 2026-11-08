"""The report envelope every harness function result extends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from stores.session_log_store.context import Context
from stores.session_log_store.outcome import Outcome

_ENVELOPE_KEYS = frozenset({"context", "outcome"})


def _thaw_data(value: Any) -> Any:
    """Convert immutable boundary data into plain JSON containers."""
    if isinstance(value, Mapping):
        return {key: _thaw_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_data(item) for item in value]
    return value


@dataclass(frozen=True)
class Report:
    """The report envelope: context, outcome, and the function-owned payload."""

    context: Context
    outcome: Outcome
    payload: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Render the contract report object: envelope plus flattened payload."""
        rendered: dict[str, Any] = {
            "context": self.context.to_dict(),
            "outcome": self.outcome.to_dict(),
        }
        rendered.update(_thaw_data(self.payload))
        return rendered

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Report":
        """Build a report from a contract report object."""
        payload = {
            key: _thaw_data(value)
            for key, value in data.items()
            if key not in _ENVELOPE_KEYS
        }
        return cls(
            context=Context.from_dict(data["context"]),
            outcome=Outcome.from_dict(data["outcome"]),
            payload=payload,
        )


__all__ = ["Report"]
