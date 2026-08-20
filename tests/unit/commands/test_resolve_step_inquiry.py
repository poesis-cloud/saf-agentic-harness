"""Tests for `ResolveStepInquiry` — function 3's input type."""

from __future__ import annotations

from commands.inquiry import Inquiry
from commands.resolve_step_inquiry import ResolveStepInquiry


class TestResolveStepInquiry:
    """Function 3's inquiry: the envelope plus the workflow slug."""

    def test_adds_the_workflow_slug_and_never_an_instance_id(self) -> None:
        """Spec (function 3, invariant 8): `resolve-step` never receives an instance id
        — the instance is deduced — so the workflow slug is the only field the inquiry
        adds to the shared envelope.
        """
        inquiry = ResolveStepInquiry(
            session_id="s1", parent_session_id=None, workflow_slug="planning"
        )

        assert isinstance(inquiry, Inquiry)
        assert inquiry.workflow_slug == "planning"
        assert not hasattr(inquiry, "workflow_instance_id")
