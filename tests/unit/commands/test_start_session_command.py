"""Tests for `StartSessionCommand` — function 0, `start-session`."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.start_session_command import StartSessionCommand
from commands.start_session_inquiry import StartSessionInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestStartSessionCommand:
    """Function 0's command: the registration boundary's contract binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): the command parses and contract-validates the
        function's `in` object into its own `StartSessionInquiry`.
        """
        command = StartSessionCommand(RecordingService("start-session"), schema_validator)

        inquiry = command.parse_inquiry(
            {"sessionId": "s1", "parentSessionId": "p1", "agent": "planner"}
        )

        assert inquiry == StartSessionInquiry(
            session_id="s1", parent_session_id="p1", agent="planner"
        )

    def test_refuses_an_inquiry_that_names_no_framework_agent(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Outcomes rule 1): a missing `agent` is one of the named
        `invalid-inquiry` cases — everything wrong with the inquiry itself.
        """
        command = StartSessionCommand(RecordingService("start-session"), schema_validator)

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1"})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_session_lifecycle_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): services never receive an `Inquiry` — the command
        unpacks it into typed parameters and answers the service's own report object.
        """
        lifecycle = RecordingService("start-session", "started")
        command = StartSessionCommand(lifecycle, schema_validator)

        report = command.execute_function(
            StartSessionInquiry(session_id="s1", parent_session_id="p1", agent="planner")
        )

        assert lifecycle.calls == [
            {"agent": "planner", "session_id": "s1", "parent_session_id": "p1"}
        ]
        assert report is lifecycle.report
