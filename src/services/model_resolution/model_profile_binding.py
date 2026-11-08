"""The canonical model profile function 4 binds to a step's dispatch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelProfileBinding:
    """Carry the selected catalog profile: slug, weighted score, cost rank, and rationale.

    Spec (function 4, Out): a catalog profile, never a host model id — a host-specific
    binding maps it to the host-specific id at dispatch.
    """

    slug: str
    score: float
    cost_rank: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `profile` object with its camelCase keys."""
        return {
            "slug": self.slug,
            "score": self.score,
            "costRank": self.cost_rank,
            "reason": self.reason,
        }


__all__ = ["ModelProfileBinding"]
