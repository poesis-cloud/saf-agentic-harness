"""Tests for `CheckStepArtifactCommand` — function 9, `check-step-artifact`."""

from __future__ import annotations

from pathlib import Path

import pytest

from command_fixtures import RecordingService
from commands.check_step_artifact_command import CheckStepArtifactCommand
from commands.check_step_artifact_inquiry import CheckStepArtifactInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestCheckStepArtifactCommand:
    """Function 9's command: the commit gate's contract binding."""

    def test_parses_the_whole_staged_write_set_from_its_in_object(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (contracts/api/check-step-artifact.input): `artifactPaths` is the whole
        set of one tool call, validated and committed (or discarded) atomically as one
        unit; the command parses it into an immutable tuple of paths.
        """
        command = CheckStepArtifactCommand(
            RecordingService("check-step-artifact"), schema_validator
        )

        inquiry = command.parse_inquiry(
            {"sessionId": "s1", "artifactPaths": ["a/one.md", "a/two.md"]}
        )

        assert inquiry == CheckStepArtifactInquiry(
            session_id="s1",
            parent_session_id=None,
            artifact_paths=(Path("a/one.md"), Path("a/two.md")),
        )

    def test_refuses_an_empty_write_set(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (contracts/api/check-step-artifact.input): `minItems: 1` — a write set
        with no path is not a write; a single-path write is a set of one.
        """
        command = CheckStepArtifactCommand(
            RecordingService("check-step-artifact"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "artifactPaths": []})

        assert failure.value.code == "invalid-inquiry"

    def test_refuses_a_scalar_path_where_the_contract_declares_a_set(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 9): call-level atomicity is over the SET — the singular
        `artifactPath` of function 8 is not this function's contract.
        """
        command = CheckStepArtifactCommand(
            RecordingService("check-step-artifact"), schema_validator
        )

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "artifactPath": "a/one.md"})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_the_inquiry_into_the_artifact_checker_call(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `services`): the command unpacks the inquiry into typed
        parameters and returns the checker's own report object.
        """
        checker = RecordingService("check-step-artifact", "valid")
        command = CheckStepArtifactCommand(checker, schema_validator)

        report = command.execute_function(
            CheckStepArtifactInquiry(
                session_id="s1",
                parent_session_id=None,
                artifact_paths=(Path("a/one.md"),),
            )
        )

        assert checker.calls == [
            {
                "session_id": "s1",
                "parent_session_id": None,
                "artifact_paths": (Path("a/one.md"),),
            }
        ]
        assert report is checker.report
