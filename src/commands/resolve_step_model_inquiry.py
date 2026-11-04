"""Function 4's input type: the model-binding inquiry."""

from __future__ import annotations

from dataclasses import dataclass, field

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class ResolveStepModelInquiry(Inquiry):
    """The envelope plus the host's bound models: the profile is deduced, never requested.

    Spec (function 4): the profile is a pure function of static configuration, the
    step deduced from the session's logs, and the models the host binds.
    """

    bound_models: tuple[str, ...] = field(default=())


__all__ = ["ResolveStepModelInquiry"]
