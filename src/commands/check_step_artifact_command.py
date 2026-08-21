"""Function 9's command: `check-step-artifact`."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from commands.check_step_artifact_inquiry import CheckStepArtifactInquiry
from commands.command import Command
from services.checking.step_artifact_checker import StepArtifactChecker
from stores.session_log_store.report import Report
from utils.schema_validator import SchemaValidator


class CheckStepArtifactCommand(Command):
    """Validate the staged write set at the commit gate.

    Spec (Workspace Git plane, principle 2): the transaction either commits — one
    validated write = one commit — or discards the whole set.
    """

    FUNCTION = "check-step-artifact"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/check-step-artifact.input/v1"

    def __init__(
        self, checker: StepArtifactChecker, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the artifact checker it gates through."""
        super().__init__(schema_validator)
        self._checker = checker

    def _build_inquiry(self, data: Mapping[str, Any]) -> CheckStepArtifactInquiry:
        """Build function 9's inquiry from its validated `in` object."""
        return CheckStepArtifactInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
            artifact_paths=tuple(Path(path) for path in data["artifactPaths"]),
        )

    def execute_function(self, inquiry: CheckStepArtifactInquiry) -> Report:
        """Unpack the inquiry into the commit-gate check over the whole write set."""
        return self._checker.check_step_artifact(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
            artifact_paths=inquiry.artifact_paths,
        )


__all__ = ["CheckStepArtifactCommand"]
