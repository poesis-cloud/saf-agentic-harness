"""Function 10 — `check-step-postconditions`: did this step deliver?"""

from __future__ import annotations

from typing import ClassVar, cast

from services.checking.check_step_postconditions_report import (
    CheckStepPostconditionsReport,
)
from services.checking.condition_check_report import ConditionCheckReport
from services.checking.step_condition_checker import StepConditionChecker


class StepPostconditionChecker(StepConditionChecker):
    """Evaluate the ended step's postconditions and journal the step's outcome.

    Spec (function 10, invariant 2): postconditions are evaluated ONCE per step
    pass, at the step-ended boundary — the state the step left is final; a duplicate
    delivery finds no in-flight step and answers `not-applicable`.
    """

    FUNCTION: ClassVar[str] = "check-step-postconditions"
    CONDITION_KIND: ClassVar[str] = "postcondition"
    REPORT_TYPE: ClassVar[type[ConditionCheckReport]] = CheckStepPostconditionsReport

    def check_step_postconditions(
        self, session_id: str, parent_session_id: str | None
    ) -> CheckStepPostconditionsReport:
        """Check the ended step's declared postconditions against final state."""
        return cast(
            CheckStepPostconditionsReport,
            self._execute_check(session_id, parent_session_id),
        )


__all__ = ["StepPostconditionChecker"]
