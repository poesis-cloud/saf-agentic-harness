"""Tests for `ResolveStepInstructionsCommand` — function 6."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.resolve_step_instructions_command import ResolveStepInstructionsCommand
from commands.resolve_step_instructions_inquiry import ResolveStepInstructionsInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestResolveStepInstructionsCommand:
    """Function 6's command: the step session's instruction context binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): the command parses its `in` object into its own
        inquiry type; function 6's is the bare envelope.
        """
        command = ResolveStepInstructionsCommand(
            RecordingService("resolve-step-instructions"), schema_validator
        )

        inquiry = command.parse_inquiry({"sessionId": "s1", "parentSessionId": "p1"})

        assert inquiry == ResolveStepInstructionsInquiry(
            session_id="s1", parent_session_id="p1"
        )

    def test_refuses_an_inquiry_naming_the_step_it_wants(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 6): the step is the one the session correlates to through its
        registration — the contract declares no `stepSlug` and rejects one.
        """
        command = ResolveStepInstructionsCommand(
            RecordingService("resolve-step-instructions"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "stepSlug": "draft"})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_step_instruction_resolver_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): the command unpacks the inquiry into typed
        parameters and returns the resolver's own report object.
        """
        resolver = RecordingService("resolve-step-instructions")
        command = ResolveStepInstructionsCommand(resolver, schema_validator)

        report = command.execute_function(
            ResolveStepInstructionsInquiry(session_id="s1", parent_session_id="p1")
        )

        assert resolver.calls == [{"session_id": "s1", "parent_session_id": "p1"}]
        assert report is resolver.report
