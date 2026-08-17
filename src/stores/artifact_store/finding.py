"""One store-level observation about an artifact path."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """Name what an artifact path violated, or what the discard did to it.

    Internal to this store: what `validate_artifact` and `revert_artifact`
    produce, and what function 9 renders into `ArtifactCheck.failureMessage`.
    """

    source: str
    rule: str
    message: str


__all__ = ["Finding"]
