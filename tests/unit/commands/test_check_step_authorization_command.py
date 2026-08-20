"""Tests for `CheckStepAuthorizationCommand` — function 8, `check-step-authorization`."""

from __future__ import annotations

from pathlib import Path

import pytest

from command_fixtures import RecordingService
from commands.check_step_authorization_command import CheckStepAuthorizationCommand
from commands.check_step_authorization_inquiry import CheckStepAuthorizationInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestCheckStepAuthorizationCommand:
    """Function 8's command: the write-starting gate's contract binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (contracts/api/check-step-authorization.input): `artifactPath` and
        `action` are required beside the envelope; the path is workspace-relative.
        """
        command = CheckStepAuthorizationCommand(
            RecordingService("check-step-authorization"), schema_validator
        )

        inquiry = command.parse_inquiry(
            {
                "sessionId": "s1",
                "parentSessionId": "p1",
                "artifactPath": "portfolio/epics/one.md",
                "action": "create",
            }
        )

        assert inquiry == CheckStepAuthorizationInquiry(
            session_id="s1",
            parent_session_id="p1",
            artifact_path=Path("portfolio/epics/one.md"),
            action="create",
        )

    def test_refuses_an_action_outside_the_declared_vocabulary(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Outcomes rule 1): `unknown-action` is an `inquiry-error`; the ACL
        contract's action vocabulary is create, update, delete — reads are never
        modeled (C3).
        """
        command = CheckStepAuthorizationCommand(
            RecordingService("check-step-authorization"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry(
                {"sessionId": "s1", "artifactPath": "a/b.md", "action": "read"}
            )

        assert failure.value.code == "invalid-inquiry"

    def test_refuses_an_inquiry_naming_its_own_actor(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 8): the actor comes from the session's registration, never
        from the inquiry — a caller-authored actor is rejected by the contract.
        """
        command = CheckStepAuthorizationCommand(
            RecordingService("check-step-authorization"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry(
                {
                    "sessionId": "s1",
                    "artifactPath": "a/b.md",
                    "action": "create",
                    "actor": "planner",
                }
            )

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_authorization_checker_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): the command unpacks the inquiry into typed
        parameters — the path crosses as a `Path`, never as a raw dict field.
        """
        checker = RecordingService("check-step-authorization", "allowed")
        command = CheckStepAuthorizationCommand(checker, schema_validator)

        report = command.execute_function(
            CheckStepAuthorizationInquiry(
                session_id="s1",
                parent_session_id="p1",
                artifact_path=Path("portfolio/epics/one.md"),
                action="create",
            )
        )

        assert checker.calls == [
            {
                "session_id": "s1",
                "parent_session_id": "p1",
                "artifact_path": Path("portfolio/epics/one.md"),
                "action": "create",
            }
        ]
        assert report is checker.report
