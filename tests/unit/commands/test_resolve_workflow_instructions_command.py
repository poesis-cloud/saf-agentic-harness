"""Tests for `ResolveWorkflowInstructionsCommand` — function 1."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.resolve_workflow_instructions_command import (
    ResolveWorkflowInstructionsCommand,
)
from commands.resolve_workflow_instructions_inquiry import (
    ResolveWorkflowInstructionsInquiry,
)
from errors import InquiryError
from utils import SchemaValidator


class TestResolveWorkflowInstructionsCommand:
    """Function 1's command: the orchestrator's instruction context binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): the command parses its `in` object into its own
        inquiry type; function 1's is the bare envelope.
        """
        command = ResolveWorkflowInstructionsCommand(
            RecordingService("resolve-workflow-instructions"), schema_validator
        )

        inquiry = command.parse_inquiry({"sessionId": "s1"})

        assert inquiry == ResolveWorkflowInstructionsInquiry(
            session_id="s1", parent_session_id=None
        )

    def test_refuses_an_inquiry_naming_the_workflow_it_wants(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 1): the workflow context is correlated from the session's own
        registration, so the contract declares no `workflowSlug` and rejects one.
        """
        command = ResolveWorkflowInstructionsCommand(
            RecordingService("resolve-workflow-instructions"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "workflowSlug": "planning"})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_workflow_instruction_resolver_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): the command unpacks the inquiry into typed
        parameters and returns the resolver's own report object.
        """
        resolver = RecordingService("resolve-workflow-instructions")
        command = ResolveWorkflowInstructionsCommand(resolver, schema_validator)

        report = command.execute_function(
            ResolveWorkflowInstructionsInquiry(session_id="s1", parent_session_id=None)
        )

        assert resolver.calls == [{"session_id": "s1", "parent_session_id": None}]
        assert report is resolver.report
