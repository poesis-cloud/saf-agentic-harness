"""Tests for `ResolveStepModelCommand` — function 4, `resolve-step-model`."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.resolve_step_model_command import ResolveStepModelCommand
from commands.resolve_step_model_inquiry import ResolveStepModelInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestResolveStepModelCommand:
    """Function 4's command: the dispatch boundary's model binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): one function = one input type = one output type =
        two contracts; function 4's inquiry is the bare envelope.
        """
        command = ResolveStepModelCommand(
            RecordingService("resolve-step-model"), schema_validator
        )

        inquiry = command.parse_inquiry({"sessionId": "s1", "parentSessionId": "p1"})

        assert inquiry == ResolveStepModelInquiry(session_id="s1", parent_session_id="p1")

    def test_refuses_an_inquiry_naming_the_model_it_wants(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 4): the profile is a pure function of configuration and the
        deduced step — a caller-authored model choice is not part of the contract.
        """
        command = ResolveStepModelCommand(
            RecordingService("resolve-step-model"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "profile": "fast-coder"})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_model_resolver_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): functions 3 and 4 are fully independent — this
        command invokes the model resolver and nothing else.
        """
        resolver = RecordingService("resolve-step-model")
        command = ResolveStepModelCommand(resolver, schema_validator)

        report = command.execute_function(
            ResolveStepModelInquiry(session_id="s1", parent_session_id="p1")
        )

        assert resolver.calls == [{"session_id": "s1", "parent_session_id": "p1"}]
        assert report is resolver.report
