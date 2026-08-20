"""Function 8's input type: the write-authorization inquiry."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class CheckStepAuthorizationInquiry(Inquiry):
    """Carry the write being authorized: its path and its action.

    Spec (function 8): the acting agent is never an input — it is the actor the
    session's own registration recorded.
    """

    artifact_path: Path
    action: str


__all__ = ["CheckStepAuthorizationInquiry"]
