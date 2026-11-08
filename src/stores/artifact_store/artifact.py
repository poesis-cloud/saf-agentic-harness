"""One schema-valid artifact of committed workspace state."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


def _freeze_data(value: Any) -> Any:
    """Convert parsed artifact data into read-only boundary containers."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_data(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_data(item) for item in value)
    return value


@dataclass(frozen=True)
class Artifact:
    """Expose one artifact's workspace ref, its kind, and its immutable data."""

    ref: str
    kind: str
    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        """Deep-freeze `data`: a read of committed state is never mutable (C6)."""
        object.__setattr__(self, "data", _freeze_data(self.data))


__all__ = ["Artifact"]
