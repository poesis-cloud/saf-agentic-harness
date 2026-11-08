"""Function 11's input type: the closing inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class EndSessionInquiry(Inquiry):
    """The bare envelope: ending takes the session id alone.

    Spec (contracts/api/end-session.input): `parentSessionId` is accepted but unused
    — the parent chain was recorded by this session's own start entry.
    """


__all__ = ["EndSessionInquiry"]
