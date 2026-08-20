"""Tests for `EndSessionInquiry` — function 11's input type."""

from __future__ import annotations

import dataclasses

from commands.end_session_inquiry import EndSessionInquiry
from commands.inquiry import Inquiry


class TestEndSessionInquiry:
    """Function 11's inquiry: the bare envelope, unextended."""

    def test_adds_no_field_of_its_own(self) -> None:
        """Spec (Classes, `commands`): seven of the twelve inquiries add no field of
        their own — their identity is their contract's `$id`, not their shape.
        """
        inquiry = EndSessionInquiry(session_id="s1", parent_session_id="p1")

        assert isinstance(inquiry, Inquiry)
        assert tuple(field.name for field in dataclasses.fields(inquiry)) == (
            "session_id",
            "parent_session_id",
        )
