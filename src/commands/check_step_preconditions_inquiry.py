"""Function 5's input type: the step-precondition inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class CheckStepPreconditionsInquiry(Inquiry):
    """The bare envelope: the gated step is the session's in-flight step.

    Spec (The harness functions): seven of the twelve inquiries add nothing at all and
    are the bare envelope.
    """


__all__ = ["CheckStepPreconditionsInquiry"]
