"""Tests for `ResolveStepModelInquiry` — function 4's input type."""

from __future__ import annotations

import dataclasses

from commands.inquiry import Inquiry
from commands.resolve_step_model_inquiry import ResolveStepModelInquiry


class TestResolveStepModelInquiry:
    """Function 4's inquiry: the bare envelope, unextended."""

    def test_adds_no_field_of_its_own(self) -> None:
        """Spec (function 4): the profile is a pure function of static configuration and
        the step deduced from the session's logs — nothing about the model is an input.
        """
        inquiry = ResolveStepModelInquiry(session_id="s1", parent_session_id=None)

        assert isinstance(inquiry, Inquiry)
        assert tuple(field.name for field in dataclasses.fields(inquiry)) == (
            "session_id",
            "parent_session_id",
        )
