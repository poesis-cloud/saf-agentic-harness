"""One model profile from `conf/model-profiles.conf.yaml`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ModelProfile:
    """Carry one model's cost rank and capability scores.

    Spec (function 7 preconditions): the model catalog is loaded and validated fail-fast;
    the capability tag vocabulary is owned by `model-profiles.conf.schema.json`.
    """

    slug: str
    cost_rank: int
    capabilities: Mapping[str, float]
    description: str | None = None
    note: str | None = None


__all__ = ["ModelProfile"]
