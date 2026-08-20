"""Function 2's input type: the workflow-skill inquiry."""

from __future__ import annotations

from dataclasses import dataclass

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class ResolveWorkflowSkillsInquiry(Inquiry):
    """The bare envelope: skills come from the correlated workflow configuration.

    Spec (The harness functions): seven of the twelve inquiries add nothing at all and
    are the bare envelope.
    """


__all__ = ["ResolveWorkflowSkillsInquiry"]
