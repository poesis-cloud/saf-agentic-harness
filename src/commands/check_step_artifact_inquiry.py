"""Function 9's input type: the commit-gate inquiry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class CheckStepArtifactInquiry(Inquiry):
    """Carry the whole staged write set of one tool call.

    Spec (contracts/api/check-step-artifact.input): every artifact path the just-landed
    write staged — validated and committed (or discarded) atomically as one unit; a
    single-path write is a set of one.
    """

    artifact_paths: tuple[Path, ...]


__all__ = ["CheckStepArtifactInquiry"]
