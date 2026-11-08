"""Function 3's input type: the step-resolution inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class ResolveStepInquiry(Inquiry):
    """Carry the workflow whose next step is resolved.

    Spec (function 3, invariant 8): `resolve-step` never receives an instance id —
    the instance is deduced from the journaled outcomes — so the workflow slug is the
    only field this inquiry adds.
    """

    workflow_slug: str


__all__ = ["ResolveStepInquiry"]
