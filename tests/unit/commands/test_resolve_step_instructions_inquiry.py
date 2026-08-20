"""Tests for `ResolveStepInstructionsInquiry` — function 6's input type."""

from __future__ import annotations

import dataclasses

from commands.inquiry import Inquiry
from commands.resolve_step_instructions_inquiry import ResolveStepInstructionsInquiry


class TestResolveStepInstructionsInquiry:
    """Function 6's inquiry: the bare envelope, unextended."""

    def test_adds_no_field_of_its_own(self) -> None:
        """Spec (Classes, `commands`): seven of the twelve inquiries add no field of
        their own — the step is deduced from the session, never named by the caller.
        """
        inquiry = ResolveStepInstructionsInquiry(session_id="s1", parent_session_id="p1")

        assert isinstance(inquiry, Inquiry)
        assert tuple(field.name for field in dataclasses.fields(inquiry)) == (
            "session_id",
            "parent_session_id",
        )
