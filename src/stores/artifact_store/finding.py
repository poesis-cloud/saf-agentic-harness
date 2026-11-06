"""One store-level observation about an artifact path."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """Name what an artifact path violated, or what the discard did to it.

    Internal to this store: what `validate_artifact` and `revert_artifact`
    produce, and what function 9 renders into `ArtifactCheck.failureMessage`.
    A revert's `origin` names the ref a restored path came from — the step
    ref or `HEAD` — and is none for a deletion or a validation finding.
    """

    source: str
    rule: str
    message: str
    origin: str | None = None


__all__ = ["Finding"]
