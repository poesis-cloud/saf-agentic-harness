"""Tests for `CheckStepPostconditionsCommand` — function 10, `check-step-postconditions`."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.check_step_postconditions_command import CheckStepPostconditionsCommand
from commands.check_step_postconditions_inquiry import CheckStepPostconditionsInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestCheckStepPostconditionsCommand:
    """Function 10's command: the step-ended evaluation's contract binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `checking`): functions 5 and 10 are structurally identical
        payloads bound to DISTINCT contracts, so each command binds its own `$id`.
        """
        command = CheckStepPostconditionsCommand(
            RecordingService("check-step-postconditions"), schema_validator
        )

        inquiry = command.parse_inquiry({"sessionId": "s1"})

        assert inquiry == CheckStepPostconditionsInquiry(
            session_id="s1", parent_session_id=None
        )
        assert command.INPUT_CONTRACT_ID != CheckStepPostconditionsCommand.FUNCTION

    def test_refuses_an_inquiry_carrying_a_caller_authored_verdict(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (C1/C2): postconditions are evaluated strictly against persisted
        workspace state — no caller-supplied outcome is part of the inquiry.
        """
        command = CheckStepPostconditionsCommand(
            RecordingService("check-step-postconditions"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "outcome": "pass"})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_postcondition_checker_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): the command unpacks the inquiry into typed
        parameters and returns the checker's own report object.
        """
        checker = RecordingService("check-step-postconditions", "pass")
        command = CheckStepPostconditionsCommand(checker, schema_validator)

        report = command.execute_function(
            CheckStepPostconditionsInquiry(session_id="s1", parent_session_id=None)
        )

        assert checker.calls == [{"session_id": "s1", "parent_session_id": None}]
        assert report is checker.report
