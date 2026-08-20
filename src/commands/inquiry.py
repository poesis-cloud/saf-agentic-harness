"""The inquiry envelope every harness function input extends."""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass


@dataclass(frozen=True)
class Inquiry(ABC):
    """Carry the session attribution pair every harness inquiry shares.

    Spec (The harness functions): every session-bound function's `in` carries
    `sessionId` and nullable `parentSessionId` directly — that pair IS the shared
    inquiry envelope (`contracts/inquiry.schema.json`) every input contract roots,
    exactly as `Report` mirrors `report.schema.json` on the output side.
    """

    session_id: str
    parent_session_id: str | None


__all__ = ["Inquiry"]
