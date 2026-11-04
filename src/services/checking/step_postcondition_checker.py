"""Function 10 — `check-step-postconditions`: did this step deliver?"""

from __future__ import annotations

from typing import ClassVar, cast

from config import Step, WorkflowCatalog, WorkspaceLayout
from errors import ConfigurationError
from services.checking.artifact_delivery import ArtifactDelivery
from services.checking.check_step_postconditions_report import (
    CheckStepPostconditionsReport,
)
from services.checking.condition_check_report import ConditionCheckReport
from services.checking.condition_evaluator import ConditionEvaluator
from services.checking.step_condition_checker import StepConditionChecker
from stores.session_log_store import LogEntry, SessionLogStore
from utils.clock import Clock

_ARTIFACT_FUNCTION = "check-step-artifact"
_VALID = "valid"
_PASS = "pass"
_FAIL = "fail"
_UNRESOLVABLE_RESOURCE = "unresolvable-resource"


class StepPostconditionChecker(StepConditionChecker):
    """Evaluate the ended step's delivery and postconditions; journal its outcome.

    Spec (function 10, invariant 2): postconditions are evaluated ONCE per step
    pass, at the step-ended boundary — the state the step left is final; a duplicate
    delivery finds no in-flight step and answers `not-applicable`.
    """

    FUNCTION: ClassVar[str] = "check-step-postconditions"
    CONDITION_KIND: ClassVar[str] = "postcondition"
    REPORT_TYPE: ClassVar[type[ConditionCheckReport]] = CheckStepPostconditionsReport

    def __init__(
        self,
        evaluator: ConditionEvaluator,
        session_log_store: SessionLogStore,
        catalog: WorkflowCatalog,
        workspace_layout: WorkspaceLayout,
        clock: Clock | None = None,
    ) -> None:
        """Create the checker; the layout maps committed paths to artifact kinds."""
        super().__init__(evaluator, session_log_store, catalog, clock)
        self._workspace_layout = workspace_layout

    def check_step_postconditions(
        self, session_id: str, parent_session_id: str | None
    ) -> CheckStepPostconditionsReport:
        """Check the ended step's delivery and declared postconditions."""
        return cast(
            CheckStepPostconditionsReport,
            self._execute_check(session_id, parent_session_id),
        )

    def _check_artifact_delivery(
        self, session_id: str, resolution: LogEntry, step: Step
    ) -> ArtifactDelivery:
        """Judge delivery from the step session's own log (function 10, invariant 5).

        The log is the source: every `valid` function-9 entry of the step's session
        records the paths its commit changed. The step delivers when those paths
        name exactly one instance of its declared artifact kind; other kinds are
        governed by authorization (function 8) only.
        """
        step_sessions = self._session_log_store.find_step_session_ids(
            session_id, step.actor, resolution.timestamp
        )
        if len(step_sessions) > 1:
            return self._fail(
                step,
                f"Step '{step.slug}' correlates to {len(step_sessions)} step sessions "
                f"({', '.join(step_sessions)}); 1 step = 1 session (function 0, "
                f"invariant 5), so its delivery cannot be attributed.",
            )
        instances = sorted(
            {
                path
                for step_session in step_sessions
                for path in self._read_committed_paths(step_session)
                if self._resolve_kind(path) == step.artifact
            }
        )
        if len(instances) == 1:
            return ArtifactDelivery(
                artifact=step.artifact, artifact_path=instances[0], outcome=_PASS
            )
        if not instances:
            return self._fail(
                step,
                f"Step '{step.slug}' delivered no '{step.artifact}' instance: its "
                f"session committed no change to one.",
            )
        return self._fail(
            step,
            f"Step '{step.slug}' delivered {len(instances)} '{step.artifact}' "
            f"instances ({', '.join(instances)}); a step delivers exactly one.",
        )

    def _read_committed_paths(self, step_session_id: str) -> tuple[str, ...]:
        """Read every path the step session's function-9 commits changed."""
        log = self._session_log_store.load_session_log(step_session_id)
        return tuple(
            str(path)
            for entry in log.entries
            if entry.report.context.function == _ARTIFACT_FUNCTION
            and entry.report.outcome.status == _VALID
            for path in entry.report.payload.get("committedPaths") or ()
        )

    def _resolve_kind(self, path: str) -> str | None:
        """Resolve a committed path to its artifact kind, or none if unbound now."""
        try:
            return self._workspace_layout.resolve_resource(path)
        except ConfigurationError as failure:
            if failure.code != _UNRESOLVABLE_RESOURCE:
                raise
            return None

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
