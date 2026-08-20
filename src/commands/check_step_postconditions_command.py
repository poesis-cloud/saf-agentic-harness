"""Function 10's command: `check-step-postconditions`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.check_step_postconditions_inquiry import CheckStepPostconditionsInquiry
from commands.command import Command
from services.checking.check_step_postconditions_report import (
    CheckStepPostconditionsReport,
)
from services.checking.step_postcondition_checker import StepPostconditionChecker
from utils.schema_validator import SchemaValidator


class CheckStepPostconditionsCommand(Command):
    """Evaluate whether the ended step delivered.

    Spec (Classes, `checking`): functions 5 and 10 return structurally identical
    results bound to DISTINCT contracts — which function produced a report is read
    from `context.function`, never inferred from the type.
    """

    FUNCTION = "check-step-postconditions"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/check-step-postconditions.input/v1"

    def __init__(
        self, checker: StepPostconditionChecker, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the postcondition checker it evaluates through."""
        super().__init__(schema_validator)
        self._checker = checker

    def _build_inquiry(self, data: Mapping[str, Any]) -> CheckStepPostconditionsInquiry:
        """Build function 10's inquiry from its validated `in` object."""
        return CheckStepPostconditionsInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(
        self, inquiry: CheckStepPostconditionsInquiry
    ) -> CheckStepPostconditionsReport:
        """Unpack the inquiry into the postcondition check."""
        return self._checker.check_step_postconditions(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
        )


__all__ = ["CheckStepPostconditionsCommand"]
