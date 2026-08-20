"""Tests for `ResolveWorkflowSkillsCommand` — function 2."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.resolve_workflow_skills_command import ResolveWorkflowSkillsCommand
from commands.resolve_workflow_skills_inquiry import ResolveWorkflowSkillsInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestResolveWorkflowSkillsCommand:
    """Function 2's command: the orchestrator's skill context binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): one function = one input type = two contracts;
        function 2's inquiry is the bare envelope.
        """
        command = ResolveWorkflowSkillsCommand(
            RecordingService("resolve-workflow-skills"), schema_validator
        )

        inquiry = command.parse_inquiry({"sessionId": "s1", "parentSessionId": None})

        assert inquiry == ResolveWorkflowSkillsInquiry(
            session_id="s1", parent_session_id=None
        )

    def test_refuses_an_inquiry_naming_the_skills_it_wants(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 2): skills are resolved from the workflow configuration the
        session correlates to — never chosen by the caller.
        """
        command = ResolveWorkflowSkillsCommand(
            RecordingService("resolve-workflow-skills"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "skills": ["orchestrate"]})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_workflow_skill_resolver_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): the command unpacks the inquiry into typed
        parameters and returns the resolver's own report object.
        """
        resolver = RecordingService("resolve-workflow-skills")
        command = ResolveWorkflowSkillsCommand(resolver, schema_validator)

        report = command.execute_function(
            ResolveWorkflowSkillsInquiry(session_id="s1", parent_session_id=None)
        )

        assert resolver.calls == [{"session_id": "s1", "parent_session_id": None}]
        assert report is resolver.report
