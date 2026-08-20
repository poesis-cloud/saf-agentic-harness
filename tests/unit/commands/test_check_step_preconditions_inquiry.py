"""Tests for `CheckStepPreconditionsInquiry` — function 5's input type."""

from __future__ import annotations

import dataclasses

from commands.check_step_preconditions_inquiry import CheckStepPreconditionsInquiry
from commands.inquiry import Inquiry


class TestCheckStepPreconditionsInquiry:
    """Function 5's inquiry: the bare envelope, unextended."""

    def test_adds_no_field_of_its_own(self) -> None:
        """Spec (Classes, `commands`): seven of the twelve inquiries add no field of
        their own — the in-flight step being gated is deduced from the session's log,
        never named by the caller.
        """
        inquiry = CheckStepPreconditionsInquiry(session_id="s1", parent_session_id=None)

        assert isinstance(inquiry, Inquiry)
        assert tuple(field.name for field in dataclasses.fields(inquiry)) == (
            "session_id",
            "parent_session_id",
        )
