"""Function 7's input type: the step-skill inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class ResolveStepSkillsInquiry(Inquiry):
    """The bare envelope: skills come from the correlated step's declaration.

    Spec (The harness functions): seven of the twelve inquiries add nothing at all and
    are the bare envelope.
    """


__all__ = ["ResolveStepSkillsInquiry"]
