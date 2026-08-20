"""Tests for the four bare-envelope context-resolution inquiries (functions 1-2, 6-7)."""

from __future__ import annotations

import dataclasses

from commands.inquiry import Inquiry
from commands.resolve_workflow_instructions_inquiry import (
    ResolveWorkflowInstructionsInquiry,
)


class TestResolveWorkflowInstructionsInquiry:
    """Function 1's inquiry: the bare envelope, unextended."""

    def test_adds_no_field_of_its_own(self) -> None:
        """Spec (Classes, `commands`): seven of the twelve inquiries add no field of
        their own — the workflow context is recovered through `sessionId`, never
        re-declared by the caller.
        """
        inquiry = ResolveWorkflowInstructionsInquiry(session_id="s1", parent_session_id=None)

        assert isinstance(inquiry, Inquiry)
        assert tuple(field.name for field in dataclasses.fields(inquiry)) == (
            "session_id",
            "parent_session_id",
        )
