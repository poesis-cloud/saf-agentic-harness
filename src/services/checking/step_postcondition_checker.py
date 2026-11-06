"""Function 10 — `check-step-postconditions`: did this step deliver? The acceptance point."""

from __future__ import annotations

from typing import ClassVar, cast

from config import Step, WorkflowCatalog
from errors import HarnessError
from services.checking.artifact_delivery import ArtifactDelivery
from services.checking.check_step_postconditions_report import (
    CheckStepPostconditionsReport,
)
from services.checking.condition_check_report import ConditionCheckReport
from services.checking.condition_evaluator import ConditionEvaluator
from services.checking.promotion import Promotion
from services.checking.step_condition_checker import StepConditionChecker
from stores.artifact_store import ArtifactStore, StepRef
from stores.session_log_store import LogEntry, Report, SessionLogStore
from utils.clock import Clock

_ARTIFACT_FUNCTION = "check-step-artifact"
_VALID = "valid"
_PASS = "pass"
_FAIL = "fail"
_IN_FLIGHT = "in-flight"
_PROMOTED = "promoted"
_WITHHELD = "withheld"


class StepPostconditionChecker(StepConditionChecker):
    """Rebase, evaluate delivery and postconditions, then promote or withhold.

    Spec (function 10): postconditions are evaluated ONCE per step pass, at the
    step-ended boundary, over the step state — the latest workspace state plus
    the step instance's validated commits rebased onto it; a passing step
    instance is promoted by one compare-and-swap fast-forward of the workspace
    branch, a failing one is withheld. A duplicate delivery finds no in-flight
    step and answers `not-applicable`.
    """

    FUNCTION: ClassVar[str] = "check-step-postconditions"
    CONDITION_KIND: ClassVar[str] = "postcondition"
    REPORT_TYPE: ClassVar[type[ConditionCheckReport]] = CheckStepPostconditionsReport

    def __init__(
        self,
        evaluator: ConditionEvaluator,
        session_log_store: SessionLogStore,
        catalog: WorkflowCatalog,
        artifact_store: ArtifactStore,
        clock: Clock | None = None,
    ) -> None:
        """Create the checker; the artifact store carries the step refs and promotion."""
        super().__init__(evaluator, session_log_store, catalog, artifact_store, clock)

    def check_step_postconditions(
        self, session_id: str, parent_session_id: str | None
    ) -> CheckStepPostconditionsReport:
        """Check the ended step's delivery and declared postconditions."""
        return cast(
            CheckStepPostconditionsReport,
            self._execute_check(session_id, parent_session_id),
        )

    def _evaluate_step(
        self,
        session_id: str,
        parent_session_id: str | None,
        resolution: LogEntry,
        step: Step,
        instance_id: str | None,
    ) -> Report:
        """Run function 10's order of operations on the in-flight step instance.

        1. rebase the step ref onto the workspace branch tip, in memory;
        2. evaluate delivery (invariant 5) and the postconditions on the step state;
        3. on `pass`, promote by compare-and-swap — THE acceptance point;
        4. after it, sync the promoted paths' index entries and release the locks,
           best-effort; 5. on `fail`, withhold: nothing else is touched.
        """
        workers = self._session_log_store.find_worker_session_ids(
            session_id, step.actor, resolution.timestamp
        )
        worker = workers[0] if len(workers) == 1 else None
        step_ref = None if worker is None else self._artifact_store.find_step_ref(worker)
        head = self._artifact_store.read_workspace_commit()
        if step_ref is not None:
            step_ref = self._artifact_store.rebase_step_ref(step_ref, head)

        delivery = self._check_artifact_delivery(step, workers, step_ref, head)
        current = {
            "slug": step.slug,
            "id": worker,
            "workflow": instance_id,
            "actor": step.actor,
            "outcome": _IN_FLIGHT,
            "artifact": {"kind": step.artifact, "path": delivery.artifact_path},
        }
        revision = head if step_ref is None else step_ref.tip
        scope = self._build_condition_scope(revision, instance_id, current)
        view = self._session_log_store.load_workflow_instance_view(instance_id)
        checks = self._evaluator.evaluate_conditions(
            self._select_conditions(step), view, scope
        )
        passed = delivery.outcome == _PASS and all(
            check.outcome == _PASS for check in checks
        )
        promotion = Promotion(outcome=_WITHHELD, workspace_commit=head)
        if passed and step_ref is not None:
            promotion = self._promote(step_ref, head)

        report = self._build_condition_report(
            session_id,
            parent_session_id,
            instance_id,
            checks,
            delivery,
            promotion=promotion,
        )
        self._journal_report(session_id, report)
        return report

    def _promote(self, step_ref: StepRef, head: str) -> Promotion:
        """Promote the step ref, then sync the index and release locks best-effort.

        Function 10, steps 3–4: a failed compare-and-swap raises
        `step-rebase-conflict` with the branch and the locks untouched; a
        failure after the acceptance point never changes the outcome — its
        residue is C6's detect-and-remediate plane.
        """
        promoted = self._artifact_store.promote_step_ref(step_ref, head)
        try:
            self._artifact_store.sync_index(
                self._artifact_store.list_commit_changes(head, promoted), promoted
            )
        except HarnessError:
            pass
        try:
            self._artifact_store.release_artifact_locks(step_ref)
        except HarnessError:
            pass
        return Promotion(outcome=_PROMOTED, workspace_commit=promoted)

    def _check_artifact_delivery(
        self,
        step: Step,
        workers: tuple[str, ...],
        step_ref: StepRef | None,
        head: str,
    ) -> ArtifactDelivery:
        """Judge delivery over the whole step state (function 10, invariant 5).

        (a) the diff between the step ref and its base — the workspace branch
        tip, after any rebase — changes exactly one artifact instance of the
        step's declared kind; and (b) the worker session's own commits, through
        function 9, include a change to that same artifact instance.
        """
        if len(workers) > 1:
            return self._fail(
                step,
                f"Step '{step.slug}' correlates to {len(workers)} worker sessions "
                f"({', '.join(workers)}); 1 step instance = 1 worker session "
                f"(function 0, invariant 5), so its delivery cannot be attributed.",
            )
        changed: tuple[str, ...] = ()
        if step_ref is not None:
            changed = self._artifact_store.list_commit_changes(head, step_ref.tip)
        instances = sorted(
            path
            for path in changed
            if self._artifact_store.resolve_artifact_kind(path) == step.artifact
        )
        if not instances:
            return self._fail(
                step,
                f"Step '{step.slug}' delivered no '{step.artifact}' artifact instance: "
                f"its step state changes none.",
            )
        if len(instances) > 1:
            return self._fail(
                step,
                f"Step '{step.slug}' delivered {len(instances)} '{step.artifact}' "
                f"artifact instances ({', '.join(instances)}); a step instance "
                f"delivers exactly one.",
            )
        delivered = instances[0]
        if delivered not in self._read_committed_paths(workers[0]):
            return self._fail(
                step,
                f"Step '{step.slug}' did not deliver '{step.artifact}': its worker "
                f"session's own commits do not change '{delivered}'.",
            )
        return ArtifactDelivery(
            artifact=step.artifact, artifact_path=delivered, outcome=_PASS
        )

    def _read_committed_paths(self, worker_session_id: str) -> frozenset[str]:
        """Read every path the worker session's function-9 commits changed."""
        log = self._session_log_store.load_session_log(worker_session_id)
        return frozenset(
            str(path)
            for entry in log.entries
            if entry.report.context.function == _ARTIFACT_FUNCTION
            and entry.report.outcome.status == _VALID
            for path in entry.report.payload.get("committedPaths") or ()
        )

    @staticmethod
    def _fail(step: Step, message: str) -> ArtifactDelivery:
        """Build the failed delivery verdict, which names no instance."""
        return ArtifactDelivery(
            artifact=step.artifact,
            artifact_path=None,
            outcome=_FAIL,
            failure_message=message,
        )


__all__ = ["StepPostconditionChecker"]
