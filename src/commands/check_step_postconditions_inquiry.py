"""Function 10's input type: the step-postcondition inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class CheckStepPostconditionsInquiry(Inquiry):
    """The bare envelope: delivery is evaluated against persisted state alone.

    Spec (C1): all preconditions and postconditions are evaluated strictly against
    persisted workspace state — no verdict is ever an input.
    """


__all__ = ["CheckStepPostconditionsInquiry"]
