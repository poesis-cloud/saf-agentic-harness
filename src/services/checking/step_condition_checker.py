"""The condition machinery functions 5 and 10 share over the in-flight step."""

from __future__ import annotations

from abc import ABC
from typing import Callable, ClassVar

from config import Step, WorkflowCatalog
from errors import ConfigurationError
from services.checking.checking_service import CheckingService
from services.checking.condition_check import ConditionCheck
from services.checking.condition_check_report import ConditionCheckReport
from services.checking.condition_evaluator import ConditionEvaluator
from stores.session_log_store import Log, LogEntry, Outcome, Report, SessionLogStore

_RESOLVE_STEP_FUNCTION = "resolve-step"
_STEP_RESOLUTION_STATUS = "step-resolution"
_POSTCONDITIONS_FUNCTION = "check-step-postconditions"
_NOT_APPLICABLE = "not-applicable"
_PASS = "pass"
_FAIL = "fail"


class StepConditionChecker(CheckingService, ABC):
    """Evaluate one kind of the in-flight step's declared conditions.

    Spec (function 10): the same condition machinery as function 5, applied to the
    step's declared postconditions — one aggregate outcome plus one check per
    declared condition, both evaluated strictly against persisted state (C1).
    """

    FUNCTION: ClassVar[str]
    CONDITION_KIND: ClassVar[str]
    REPORT_TYPE: ClassVar[type[ConditionCheckReport]]

    def __init__(
        self,
        evaluator: ConditionEvaluator,
        session_log_store: SessionLogStore,
        catalog: WorkflowCatalog,
        clock: Callable[[], str] | None = None,
    ) -> None:
        """Create the checker over its evaluator, its log store, and the catalog."""
        super().__init__(session_log_store, clock)
        self._evaluator = evaluator
        self._catalog = catalog

    def _check_open_session(
        self,
        session_id: str,
        parent_session_id: str | None,
        log: Log,
        workflow_instance_id: str | None,
    ) -> Report:
        """Evaluate the in-flight step's conditions, or answer `not-applicable`.

        Spec (rule 2): functions 5 and 10 answer `not-applicable` when persisted
        state names no target — the invoking session has no in-flight step, which
        also absorbs a duplicate step-ended delivery.
        """
        resolution = self._find_in_flight_resolution(log)
        if resolution is None:
            return self._build_report(
                session_id,
                parent_session_id,
                workflow_instance_id,
                Outcome(status=_NOT_APPLICABLE),
            )

        instance_id = resolution.report.context.workflow_instance_id
        step = self._find_step(instance_id, resolution)
        conditions = tuple(
            condition
            for condition in step.conditions
            if condition.kind == self.CONDITION_KIND
        )
        view = self._session_log_store.load_workflow_instance_view(instance_id)
        checks = self._evaluator.evaluate_conditions(conditions, view, step.artifact)
        report = self._build_condition_report(
            session_id, parent_session_id, instance_id, checks
        )
        self._journal_report(session_id, report)
        return report

    def _build_condition_report(
        self,
        session_id: str,
        parent_session_id: str | None,
        workflow_instance_id: str | None,
        checks: tuple[ConditionCheck, ...],
    ) -> ConditionCheckReport:
        """Aggregate the per-condition checks into this function's report."""
        status = _FAIL if any(check.outcome == _FAIL for check in checks) else _PASS
        return self.REPORT_TYPE(
            context=self._build_context(
                self.FUNCTION, session_id, parent_session_id, workflow_instance_id
            ),
            outcome=Outcome(status=status),
            condition_checks=checks,
        )

    def _build_report(
        self,
        session_id: str,
        parent_session_id: str | None,
        workflow_instance_id: str | None,
        outcome: Outcome,
    ) -> Report:
        """Build the envelope-only report of a `not-applicable` or error outcome."""
        return self.REPORT_TYPE(
            context=self._build_context(
                self.FUNCTION, session_id, parent_session_id, workflow_instance_id
            ),
            outcome=outcome,
        )

    def _find_in_flight_resolution(self, log: Log) -> LogEntry | None:
        """Find the session's in-flight step resolution — resolved, no outcome yet.

        Spec (function 10, Interface): correlation relies on function 3, invariant 9
        — one in-flight step per orchestrator session — so the session's single
        unresolved step resolution IS the step being checked.
        """
        pending: LogEntry | None = None
        for entry in log.entries:
            function = entry.report.context.function
            if (
                function == _RESOLVE_STEP_FUNCTION
                and entry.report.outcome.status == _STEP_RESOLUTION_STATUS
            ):
                pending = entry
            elif function == _POSTCONDITIONS_FUNCTION:
                pending = None
        return pending

    def _find_step(self, workflow_instance_id: str | None, resolution: LogEntry) -> Step:
        """Read the in-flight step's declaration from the workflow configuration.

        The instance id's `workflowSlug` prefix is load-bearing (context contract),
        so it names the workflow whose configuration declares the resolved step.
        """
        if workflow_instance_id is None:
            raise ConfigurationError(
                "step-correlation-missing",
                "The in-flight step resolution names no workflow instance.",
                False,
            )
        workflow = self._catalog.find_workflow(workflow_instance_id.rsplit("-", 1)[0])
        step_slug = resolution.report.payload["step"]["slug"]
        for step in workflow.steps:
            if step.slug == step_slug:
                return step
        raise ConfigurationError(
            "unknown-step",
            f"Workflow '{workflow.slug}' declares no step '{step_slug}'.",
            False,
        )


__all__ = ["StepConditionChecker"]
