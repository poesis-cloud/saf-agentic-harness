"""Tests for `CheckStepPostconditionsInquiry` — function 10's input type."""

from __future__ import annotations

import dataclasses

from commands.check_step_postconditions_inquiry import CheckStepPostconditionsInquiry
from commands.inquiry import Inquiry


class TestCheckStepPostconditionsInquiry:
    """Function 10's inquiry: the bare envelope, unextended."""

    def test_adds_no_field_of_its_own(self) -> None:
        """Spec (Classes, `commands`): functions 5 and 10 are structurally identical and
        distinguished by their contracts alone — neither inquiry adds a field.
        """
        inquiry = CheckStepPostconditionsInquiry(session_id="s1", parent_session_id=None)

        assert isinstance(inquiry, Inquiry)
        assert tuple(field.name for field in dataclasses.fields(inquiry)) == (
            "session_id",
            "parent_session_id",
        )
