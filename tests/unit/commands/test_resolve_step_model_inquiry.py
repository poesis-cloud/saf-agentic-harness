"""Tests for `ResolveStepModelInquiry` — function 4's input type."""

from __future__ import annotations

import dataclasses

from commands.inquiry import Inquiry
from commands.resolve_step_model_inquiry import ResolveStepModelInquiry


class TestResolveStepModelInquiry:
    """Function 4's inquiry: the envelope plus the host's bound models."""

    def test_adds_only_the_hosts_bound_models(self) -> None:
        """Spec (function 4): the profile is a pure function of static configuration, the
        step deduced from the session's logs, and the models the host binds — the caller
        never names the model it wants.
        """
        inquiry = ResolveStepModelInquiry(
            session_id="s1", parent_session_id=None, bound_models=("m-a",)
        )

        assert isinstance(inquiry, Inquiry)
        assert tuple(field.name for field in dataclasses.fields(inquiry)) == (
            "session_id",
            "parent_session_id",
            "bound_models",
        )
