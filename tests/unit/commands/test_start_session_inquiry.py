"""Tests for `StartSessionInquiry` — function 0's input type."""

from __future__ import annotations

from commands.inquiry import Inquiry
from commands.start_session_inquiry import StartSessionInquiry


class TestStartSessionInquiry:
    """Function 0's inquiry: the envelope plus the framework agent name."""

    def test_adds_the_framework_agent_to_the_shared_envelope(self) -> None:
        """Spec (The harness functions): function 0 is the bootstrap exception only in
        that its `in` also carries the framework agent name as a required `agent` slug,
        because no opening exists yet and the session record is the only place to attach
        that identity.
        """
        inquiry = StartSessionInquiry(
            session_id="s1", parent_session_id=None, agent="planner"
        )

        assert isinstance(inquiry, Inquiry)
        assert inquiry.agent == "planner"
        assert (inquiry.session_id, inquiry.parent_session_id) == ("s1", None)
