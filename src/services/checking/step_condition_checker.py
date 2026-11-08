"""The condition machinery functions 5 and 10 share over the in-flight step."""

from __future__ import annotations

from abc import ABC
from typing import Any, ClassVar, Mapping

from config import Step, WorkflowCatalog
from errors import ConfigurationError, StateError
from services.checking.artifact_delivery import ArtifactDelivery
from services.checking.checking_service import CheckingService
from services.checking.condition_check import ConditionCheck
from services.checking.condition_check_report import ConditionCheckReport
from services.checking.condition_evaluator import ConditionEvaluator
from services.checking.condition_scope import ConditionScope
from stores.artifact_store import ArtifactStore
from stores.session_log_store import (
    Log,
    LogEntry,
    Outcome,
    Report,
    SessionLogStore,
    StepInstance,
)
from utils.clock import Clock

_RESOLVE_STEP_FUNCTION = "resolve-step"
_STEP_RESOLUTION_STATUS = "step-resolution"
_POSTCONDITIONS_FUNCTION = "check-step-postconditions"
_NOT_APPLICABLE = "not-applicable"
_PASS = "pass"
_FAIL = "fail"
_IN_FLIGHT = "in-flight"


def render_step_instance(instance: StepInstance) -> dict[str, Any]:
    """Render one step instance as a `steps` element (function 5, invariant 2)."""
    artifact = (
        None
        if instance.artifact_path is None
        else {"kind": instance.artifact_kind, "path": instance.artifact_path}
    )
    return {
        "slug": instance.step_slug,
        "id": instance.session_id,
        "workflow": instance.workflow_instance_id,
        "actor": instance.actor,
        "outcome": instance.outcome,
        "artifact": artifact,
    }


def render_workflow_instance(workflow_instance_id: str) -> dict[str, Any]:
    """Render one workflow instance as a `workflows` element: its slug and id."""
    return {"slug": workflow_instance_id.rpartition("-")[0], "id": workflow_instance_id}


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
        artifact_store: ArtifactStore,
        clock: Clock | None = None,
    ) -> None:
        """Create the checker over its evaluator, its stores, and the catalog."""
        super().__init__(session_log_store, clock)
        self._evaluator = evaluator
        self._catalog = catalog
        self._artifact_store = artifact_store

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
        return self._evaluate_step(
            session_id, parent_session_id, resolution, step, instance_id
        )

    def _evaluate_step(
        self,
        session_id: str,
        parent_session_id: str | None,
        resolution: LogEntry,
        step: Step,
        instance_id: str | None,
    ) -> Report:
        """Evaluate the declared conditions over workspace state and journal the report.

        Function 5 / C1: preconditions are evaluated against workspace state —
        the workspace branch — with `step` the step instance about to start.
        """
        conditions = self._select_conditions(step)
        view = self._session_log_store.load_workflow_instance_view(instance_id)
        scope = self._build_condition_scope(
            self._artifact_store.read_workspace_commit(),
            instance_id,
            self._render_starting_step(step, instance_id),
        )
        checks = self._evaluator.evaluate_conditions(conditions, view, scope)
        report = self._build_condition_report(
            session_id, parent_session_id, instance_id, checks
        )
        self._journal_report(session_id, report)
        return report

    def _select_conditions(self, step: Step) -> tuple[Any, ...]:
        """Select the step's declared conditions of this checker's kind."""
        return tuple(
            condition
            for condition in step.conditions
            if condition.kind == self.CONDITION_KIND
        )

    @staticmethod
    def _render_starting_step(step: Step, workflow_instance_id: str | None) -> dict[str, Any]:
        """Render `step` in a precondition: the step instance about to start.

        Function 5, invariant 2: not yet an element of `steps` — `slug`,
        `workflow`, and `actor` set, `id` null, `outcome` `in-flight`, and
        `artifact` `{kind: <declared kind>, path: null}`.
        """
        return {
            "slug": step.slug,
            "id": None,
            "workflow": workflow_instance_id,
            "actor": step.actor,
            "outcome": _IN_FLIGHT,
            "artifact": {"kind": step.artifact, "path": None},
        }

    def _build_condition_scope(
        self,
        revision: str,
        workflow_instance_id: str | None,
        current_step: Mapping[str, Any],
    ) -> ConditionScope:
        """Assemble the condition language's variables over one evaluated state.

        `steps` and `workflows` come from this machine's logs; when the current
        step instance is one of `steps` (its own postconditions), its element is
        replaced by `current_step`, carrying the delivery being evaluated.
        """
        steps = tuple(
            current_step
            if current_step.get("id") is not None
            and instance.session_id == current_step["id"]
            else render_step_instance(instance)
            for instance in self._session_log_store.list_step_instances()
        )
        workflows = tuple(
            render_workflow_instance(workflow_instance_id_)
            for workflow_instance_id_ in self._session_log_store.list_workflow_instance_ids()
        )
        workflow = (
            {"slug": None, "id": None}
            if workflow_instance_id is None
            else render_workflow_instance(workflow_instance_id)
        )
        return ConditionScope(
            revision=revision,
            workflow=workflow,
            step=current_step,
            steps=steps,
            workflows=workflows,
        )

    def _build_condition_report(
        self,
        session_id: str,
        parent_session_id: str | None,
        workflow_instance_id: str | None,
        checks: tuple[ConditionCheck, ...],
        delivery: ArtifactDelivery | None = None,
        **extra: Any,
    ) -> ConditionCheckReport:
        """Aggregate the per-condition checks, and any delivery, into this report."""
        failed = any(check.outcome == _FAIL for check in checks) or (
            delivery is not None and delivery.outcome == _FAIL
        )
        if delivery is not None:
            extra["artifact_delivery"] = delivery
        return self.REPORT_TYPE(
            context=self._build_context(
                self.FUNCTION, session_id, parent_session_id, workflow_instance_id
            ),
            outcome=Outcome(status=_FAIL if failed else _PASS),
            condition_checks=checks,
            **extra,
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
            raise StateError(
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
