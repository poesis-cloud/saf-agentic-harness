"""How one staged path was discarded, when function 9 reverts a write set."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Revert:
    """Carry one discarded path's revert action and, when restored, its source.

    Spec (function 9, invariant 2): the discard restores a path from the step
    ref tip when the step ref changed it, otherwise from `HEAD` when tracked
    there, and deletes a new one — a deletion restores from nothing, so the
    source is absent.
    """

    action: str
    from_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `revert` object with its camelCase keys."""
        rendered: dict[str, Any] = {"action": self.action}
        if self.from_ref is not None:
            rendered["from"] = self.from_ref
        return rendered


__all__ = ["Revert"]
