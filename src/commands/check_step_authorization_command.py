"""Function 8's command: `check-step-authorization`."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from commands.check_step_authorization_inquiry import CheckStepAuthorizationInquiry
from commands.command import Command
from services.checking.step_authorization_checker import StepAuthorizationChecker
from stores.session_log_store.report import Report
from utils.schema_validator import SchemaValidator


class CheckStepAuthorizationCommand(Command):
    """Authorize one artifact write before it lands.

    Spec (Boundary Normalization): the write-starting boundary carries authorization
    and the staging baseline — it can deny; a target under the workspace logs path is
    denied always.
    """

    FUNCTION = "check-step-authorization"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/check-step-authorization.input/v1"

    def __init__(
        self, checker: StepAuthorizationChecker, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the authorization checker it decides through."""
        super().__init__(schema_validator)
        self._checker = checker

    def _build_inquiry(self, data: Mapping[str, Any]) -> CheckStepAuthorizationInquiry:
        """Build function 8's inquiry from its validated `in` object."""
        return CheckStepAuthorizationInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
            artifact_path=Path(data["artifactPath"]),
            action=data["action"],
        )

    def execute_function(self, inquiry: CheckStepAuthorizationInquiry) -> Report:
        """Unpack the inquiry into the authorization decision."""
        return self._checker.check_step_authorization(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
            artifact_path=inquiry.artifact_path,
            action=inquiry.action,
        )


__all__ = ["CheckStepAuthorizationCommand"]
