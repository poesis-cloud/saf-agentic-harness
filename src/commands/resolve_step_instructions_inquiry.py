"""Function 6's input type: the step-instruction inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class ResolveStepInstructionsInquiry(Inquiry):
    """The bare envelope: the step is the one this session correlates to.

    Spec (The harness functions): seven of the twelve inquiries add nothing at all and
    are the bare envelope.
    """


__all__ = ["ResolveStepInstructionsInquiry"]
