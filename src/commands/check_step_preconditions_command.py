"""Function 5's command: `check-step-preconditions`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.check_step_preconditions_inquiry import CheckStepPreconditionsInquiry
from commands.command import Command
from services.checking.check_step_preconditions_report import (
    CheckStepPreconditionsReport,
)
from services.checking.step_precondition_checker import StepPreconditionChecker
from utils.schema_validator import SchemaValidator


class CheckStepPreconditionsCommand(Command):
    """Gate the dispatch of the session's in-flight step.

    Spec (Boundary Normalization): the step-starting boundary is THE enforcement
    point — it can deny.
    """

    FUNCTION = "check-step-preconditions"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/check-step-preconditions.input/v1"

    def __init__(
        self, checker: StepPreconditionChecker, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the precondition checker it gates through."""
        super().__init__(schema_validator)
        self._checker = checker

    def _build_inquiry(self, data: Mapping[str, Any]) -> CheckStepPreconditionsInquiry:
        """Build function 5's inquiry from its validated `in` object."""
        return CheckStepPreconditionsInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(
        self, inquiry: CheckStepPreconditionsInquiry
    ) -> CheckStepPreconditionsReport:
        """Unpack the inquiry into the precondition check."""
        return self._checker.check_step_preconditions(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
        )


__all__ = ["CheckStepPreconditionsCommand"]
