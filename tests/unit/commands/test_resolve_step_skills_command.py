"""Tests for `ResolveStepSkillsCommand` — function 7."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.resolve_step_skills_command import ResolveStepSkillsCommand
from commands.resolve_step_skills_inquiry import ResolveStepSkillsInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestResolveStepSkillsCommand:
    """Function 7's command: the step session's skill context binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): one function = one input type = two contracts;
        function 7's inquiry is the bare envelope.
        """
        command = ResolveStepSkillsCommand(
            RecordingService("resolve-step-skills"), schema_validator
        )

        inquiry = command.parse_inquiry({"sessionId": "s1", "parentSessionId": "p1"})

        assert inquiry == ResolveStepSkillsInquiry(session_id="s1", parent_session_id="p1")

    def test_refuses_an_inquiry_carrying_an_undeclared_property(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Source layout): a rooted schema uses `unevaluatedProperties: false`, so
        anything beyond the envelope is rejected at the command boundary.
        """
        command = ResolveStepSkillsCommand(
            RecordingService("resolve-step-skills"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "skills": ["drafting"]})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_step_skill_resolver_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): the command unpacks the inquiry into typed
        parameters and returns the resolver's own report object.
        """
        resolver = RecordingService("resolve-step-skills")
        command = ResolveStepSkillsCommand(resolver, schema_validator)

        report = command.execute_function(
            ResolveStepSkillsInquiry(session_id="s1", parent_session_id="p1")
        )

        assert resolver.calls == [{"session_id": "s1", "parent_session_id": "p1"}]
        assert report is resolver.report
