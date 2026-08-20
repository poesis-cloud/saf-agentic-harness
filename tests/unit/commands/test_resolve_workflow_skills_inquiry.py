"""Tests for `ResolveWorkflowSkillsInquiry` — function 2's input type."""

from __future__ import annotations

import dataclasses

from commands.inquiry import Inquiry
from commands.resolve_workflow_skills_inquiry import ResolveWorkflowSkillsInquiry


class TestResolveWorkflowSkillsInquiry:
    """Function 2's inquiry: the bare envelope, unextended."""

    def test_adds_no_field_of_its_own(self) -> None:
        """Spec (Classes, `commands`): seven of the twelve inquiries add no field of
        their own — their identity is their contract's `$id`, not their shape.
        """
        inquiry = ResolveWorkflowSkillsInquiry(session_id="s1", parent_session_id=None)

        assert isinstance(inquiry, Inquiry)
        assert tuple(field.name for field in dataclasses.fields(inquiry)) == (
            "session_id",
            "parent_session_id",
        )
