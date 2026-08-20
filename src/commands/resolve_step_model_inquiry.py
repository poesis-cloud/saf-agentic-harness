"""Function 4's input type: the model-binding inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class ResolveStepModelInquiry(Inquiry):
    """The bare envelope: the profile is deduced, never requested.

    Spec (function 4): the profile is a pure function of static configuration and the
    step deduced from the session's logs.
    """


__all__ = ["ResolveStepModelInquiry"]
