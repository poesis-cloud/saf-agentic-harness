"""Function 10 — `check-step-postconditions`: did this step deliver?"""

from __future__ import annotations

from typing import ClassVar, cast

from config import ARTIFACT_DELIVERED_CONDITION, StateCondition, Step, StepCondition
from services.checking.check_step_postconditions_report import (
    CheckStepPostconditionsReport,
)
from services.checking.condition_check import ConditionCheck
from services.checking.condition_check_report import ConditionCheckReport
from services.checking.step_condition_checker import StepConditionChecker


class StepPostconditionChecker(StepConditionChecker):
    """Evaluate the ended step's postconditions and journal the step's outcome.

    Spec (function 10, invariant 2): postconditions are evaluated ONCE per step
    pass, at the step-ended boundary — the state the step left is final; a duplicate
    delivery finds no in-flight step and answers `not-applicable`. Invariant 5: the
    built-in artifact-delivery check runs first on every step, unconditionally.
    """

    FUNCTION: ClassVar[str] = "check-step-postconditions"
    CONDITION_KIND: ClassVar[str] = "postcondition"
    REPORT_TYPE: ClassVar[type[ConditionCheckReport]] = CheckStepPostconditionsReport
    BUILT_IN_CONDITIONS: ClassVar[tuple[StepCondition | StateCondition, ...]] = (
        ARTIFACT_DELIVERED_CONDITION,
    )

    def check_step_postconditions(
        self, session_id: str, parent_session_id: str | None
    ) -> CheckStepPostconditionsReport:
        """Check the ended step's declared postconditions against final state."""
        return cast(
            CheckStepPostconditionsReport,
            self._execute_check(session_id, parent_session_id),
        )

    def _explain_check(
        self, check: ConditionCheck, step: Step, step_session_ids: tuple[str, ...]
    ) -> ConditionCheck:
        """Word a failed delivery check by the artifact kind and the step's sessions."""
        if check.condition is not ARTIFACT_DELIVERED_CONDITION or check.outcome == "pass":
            return check
        sessions = ", ".join(f"'{session}'" for session in step_session_ids) or "none"
        return ConditionCheck(
            condition=check.condition,
            outcome=check.outcome,
            failure_message=(
                f"step '{step.slug}' delivered no '{step.artifact}' artifact: no "
                f"committed instance's provenance names a session of this step "
                f"(step sessions: {sessions})"
            ),
        )


__all__ = ["StepPostconditionChecker"]
