"""Function 0's input type: the registration inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class StartSessionInquiry(Inquiry):
    """Carry the framework agent whose session just opened.

    Spec (The harness functions): function 0 is the bootstrap exception only in that
    its `in` also carries the framework agent name as a required `agent` slug —
    no opening exists yet, and the session record is the only place to attach that
    identity.
    """

    agent: str


__all__ = ["StartSessionInquiry"]
