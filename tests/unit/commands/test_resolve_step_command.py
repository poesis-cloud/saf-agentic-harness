"""Tests for `ResolveStepCommand` — function 3, `resolve-step`."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.resolve_step_command import ResolveStepCommand
from commands.resolve_step_inquiry import ResolveStepInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestResolveStepCommand:
    """Function 3's command: the resolution boundary's contract binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): the command parses its `in` object into its own
        `ResolveStepInquiry`, carrying the workflow slug beside the envelope.
        """
        command = ResolveStepCommand(RecordingService("resolve-step"), schema_validator)

        inquiry = command.parse_inquiry({"sessionId": "s1", "workflowSlug": "planning"})

        assert inquiry == ResolveStepInquiry(
            session_id="s1", parent_session_id=None, workflow_slug="planning"
        )

    def test_refuses_an_inquiry_carrying_no_workflow_slug(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (contracts/api/resolve-step.input): `workflowSlug` is required — the
        instance is deduced, but the workflow must be named.
        """
        command = ResolveStepCommand(RecordingService("resolve-step"), schema_validator)

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1"})

        assert failure.value.code == "invalid-inquiry"

    def test_refuses_an_inquiry_naming_a_workflow_instance_id(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 3, invariant 8): `resolve-step` never receives an instance id;
        the contract's `unevaluatedProperties: false` rejects one outright.
        """
        command = ResolveStepCommand(RecordingService("resolve-step"), schema_validator)

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry(
                {
                    "sessionId": "s1",
                    "workflowSlug": "planning",
                    "workflowInstanceId": "planning-01J9XQ",
                }
            )

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_step_resolver_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): no command composes services — function 3 invokes
        the step resolver alone, never the model resolver.
        """
        resolver = RecordingService("resolve-step", "step-resolution")
        command = ResolveStepCommand(resolver, schema_validator)

        report = command.execute_function(
            ResolveStepInquiry(
                session_id="s1", parent_session_id=None, workflow_slug="planning"
            )
        )

        assert resolver.calls == [
            {"session_id": "s1", "parent_session_id": None, "workflow_slug": "planning"}
        ]
        assert report is resolver.report
