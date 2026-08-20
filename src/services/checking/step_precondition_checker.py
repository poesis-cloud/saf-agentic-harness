"""Function 5 — `check-step-preconditions`: may this step start?"""

from __future__ import annotations

from typing import ClassVar, cast

from services.checking.check_step_preconditions_report import (
    CheckStepPreconditionsReport,
)
from services.checking.condition_check_report import ConditionCheckReport
from services.checking.step_condition_checker import StepConditionChecker


class StepPreconditionChecker(StepConditionChecker):
    """Gate the step-starting boundary on the in-flight step's preconditions.

    Spec (function 5): THE enforcement point — a failing precondition denies the
    dispatch; the invocation journals to the dispatching (orchestrator) session's
    log and touches no artifact.
    """

    FUNCTION: ClassVar[str] = "check-step-preconditions"
    CONDITION_KIND: ClassVar[str] = "precondition"
    REPORT_TYPE: ClassVar[type[ConditionCheckReport]] = CheckStepPreconditionsReport

    def check_step_preconditions(
        self, session_id: str, parent_session_id: str | None
    ) -> CheckStepPreconditionsReport:
        """Check the in-flight step's declared preconditions against persisted state."""
        return cast(
            CheckStepPreconditionsReport,
            self._execute_check(session_id, parent_session_id),
        )


__all__ = ["StepPreconditionChecker"]
