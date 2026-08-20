"""Tests for `CheckStepAuthorizationInquiry` — function 8's input type."""

from __future__ import annotations

from pathlib import Path

from commands.check_step_authorization_inquiry import CheckStepAuthorizationInquiry
from commands.inquiry import Inquiry


class TestCheckStepAuthorizationInquiry:
    """Function 8's inquiry: the envelope plus the write being authorized."""

    def test_adds_the_written_path_and_the_requested_action(self) -> None:
        """Spec (The harness functions): function-specific fields live beside the
        session attribution pair in the same object — `artifactPath` and `action` for
        function 8. The acting agent is never an input: it comes from the session.
        """
        inquiry = CheckStepAuthorizationInquiry(
            session_id="s1",
            parent_session_id="p1",
            artifact_path=Path("portfolio/epics/one.md"),
            action="create",
        )

        assert isinstance(inquiry, Inquiry)
        assert inquiry.artifact_path == Path("portfolio/epics/one.md")
        assert inquiry.action == "create"
        assert not hasattr(inquiry, "actor")
