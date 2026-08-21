"""Tests for `CheckStepPreconditionsCommand` — function 5, `check-step-preconditions`."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.check_step_preconditions_command import CheckStepPreconditionsCommand
from commands.check_step_preconditions_inquiry import CheckStepPreconditionsInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestCheckStepPreconditionsCommand:
    """Function 5's command: the step-starting gate's contract binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): the command parses its `in` object into its own
        inquiry type; function 5's is the bare envelope.
        """
        command = CheckStepPreconditionsCommand(
            RecordingService("check-step-preconditions"), schema_validator
        )

        inquiry = command.parse_inquiry({"sessionId": "s1", "parentSessionId": None})

        assert inquiry == CheckStepPreconditionsInquiry(
            session_id="s1", parent_session_id=None
        )

    def test_refuses_an_inquiry_naming_the_step_it_wants_gated(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 5): the step being gated is the invoking session's in-flight
        step, deduced from persisted state — never a caller-supplied field.
        """
        command = CheckStepPreconditionsCommand(
            RecordingService("check-step-preconditions"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "stepSlug": "draft"})

        assert failure.value.code == "invalid-inquiry"

    def test_refuses_a_non_slug_session_id_at_the_contract_boundary(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Outcomes rule 1 + rule 4): a non-slug `sessionId` fails function 5's input
        contract as `invalid-inquiry` — pre-attribution and unjournalable, so it surfaces
        at the command exit plane and no report is ever built.
        """
        service = RecordingService("check-step-preconditions")
        command = CheckStepPreconditionsCommand(service, schema_validator)

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "../escape"})

        assert failure.value.code == "invalid-inquiry"
        assert service.calls == []

    def test_unpacks_the_inquiry_into_the_precondition_checker_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): the command unpacks the inquiry into typed
        parameters and returns the checker's own report object.
        """
        checker = RecordingService("check-step-preconditions", "pass")
        command = CheckStepPreconditionsCommand(checker, schema_validator)

        report = command.execute_function(
            CheckStepPreconditionsInquiry(session_id="s1", parent_session_id="p1")
        )

        assert checker.calls == [{"session_id": "s1", "parent_session_id": "p1"}]
        assert report is checker.report
