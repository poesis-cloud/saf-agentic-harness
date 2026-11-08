"""Function 1's input type: the workflow-instruction inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class ResolveWorkflowInstructionsInquiry(Inquiry):
    """The bare envelope: the workflow context is recovered through `sessionId`.

    Spec (The harness functions): seven of the twelve inquiries add nothing at all and
    are the bare envelope.
    """


__all__ = ["ResolveWorkflowInstructionsInquiry"]
