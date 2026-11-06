"""The CEL machinery functions 5 and 10 share: the condition language."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import celpy
from celpy import celtypes

from config import StateCondition, StepCondition
from errors import StateError
from services.checking.condition_check import ConditionCheck
from services.checking.condition_scope import ConditionScope
from stores.artifact_store import Artifact, ArtifactStore
from stores.session_log_store import WorkflowInstanceView

_PASS = "pass"
_FAIL = "fail"


def _thaw(value: Any) -> Any:
    """Convert frozen data into the plain containers `json_to_cel` admits."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _render_artifact(artifact: Artifact) -> dict[str, Any]:
    """Render one artifact instance as a `workspace` element: kind, path, data."""
    return {"kind": artifact.kind, "path": artifact.ref, "data": _thaw(artifact.data)}


class ConditionEvaluator:
    """Evaluate a step's declared conditions against persisted state.

    Spec (function 5, invariant 2; C1, C2): `setQuery` and `setPredicate` read the
    evaluated state through `workspace` (its artifact instances), `workflows` and
    `steps` (the instances the logs record), `workflow` and `step` (the current
    ones), and, in `setPredicate`, `selected` — never anything an agent merely
    remembers, and never the working tree.
    """

    def __init__(self, artifact_store: ArtifactStore) -> None:
        """Create the evaluator over the artifact plane it asserts."""
        self._artifact_store = artifact_store
        self._environment = celpy.Environment()

    def evaluate_conditions(
        self,
        conditions: Sequence[StepCondition | StateCondition],
        view: WorkflowInstanceView,
        scope: ConditionScope,
    ) -> tuple[ConditionCheck, ...]:
        """Evaluate every condition in order, one check per declared condition."""
        workspace_cache: dict[frozenset[str] | None, celtypes.Value] = {}
        log_plane = {
            "workflows": celpy.json_to_cel(_thaw(list(scope.workflows))),
            "steps": celpy.json_to_cel(_thaw(list(scope.steps))),
            "workflow": celpy.json_to_cel(_thaw(scope.workflow)),
            "step": celpy.json_to_cel(_thaw(scope.step)),
        }
        return tuple(
            self._evaluate_condition(condition, view, scope, log_plane, workspace_cache)
            for condition in conditions
        )

    def _evaluate_condition(
        self,
        condition: StepCondition | StateCondition,
        view: WorkflowInstanceView,
        scope: ConditionScope,
        log_plane: Mapping[str, Any],
        workspace_cache: dict[frozenset[str] | None, celtypes.Value],
    ) -> ConditionCheck:
        """Evaluate one condition into its check."""
        if isinstance(condition, StepCondition):
            return self._evaluate_step_condition(condition, view)
        kinds = condition.workspace_kinds
        if kinds not in workspace_cache:
            workspace_cache[kinds] = self._load_workspace(kinds, scope.revision)
        activation = {**log_plane, "workspace": workspace_cache[kinds]}
        return self._evaluate_state_condition(condition, activation)

    def _evaluate_step_condition(
        self, condition: StepCondition, view: WorkflowInstanceView
    ) -> ConditionCheck:
        """Check a step-binding condition against the instance view.

        Function 5, invariant 1: a `kind: postcondition` stepCondition names a
        successor and is advisory — never re-checked as a hard gate, so it can
        never fail here; the DAG edge is enforced from the successor's side.
        """
        if condition.kind != "precondition":
            return ConditionCheck(condition=condition, outcome=_PASS)
        if condition.step in view.list_executed_steps():
            return ConditionCheck(condition=condition, outcome=_PASS)
        return ConditionCheck(
            condition=condition,
            outcome=_FAIL,
            failure_message=(
                f"predecessor step '{condition.step}' is not journaled executed "
                f"in workflow instance '{view.workflow_instance_id}'"
            ),
        )

    def _evaluate_state_condition(
        self, condition: StateCondition, activation: Mapping[str, Any]
    ) -> ConditionCheck:
        """Select with `setQuery`, then decide `setPredicate` over `selected`."""
        set_query = condition.set_selector["setQuery"]
        selected = self._evaluate_expression(condition, set_query, activation)
        decided = self._evaluate_expression(
            condition, condition.set_predicate, {**activation, "selected": selected}
        )
        if not isinstance(decided, celtypes.BoolType):
            raise StateError(
                "condition-evaluation-failed",
                f"Condition '{condition.slug}' predicate did not evaluate to a "
                f"boolean: '{condition.set_predicate}'.",
                False,
            )
        if decided:
            return ConditionCheck(condition=condition, outcome=_PASS)
        return ConditionCheck(
            condition=condition,
            outcome=_FAIL,
            failure_message=(
                f"no persisted state satisfies '{condition.set_predicate}' for "
                f"condition '{condition.slug}'"
            ),
        )

    def _load_workspace(
        self, kinds: frozenset[str] | None, revision: str
    ) -> celtypes.Value:
        """Load `workspace`: the evaluated state's artifact instances.

        Only the kinds the condition's guards reference are read when the
        configuration load could fix them; every kind otherwise.
        """
        return celpy.json_to_cel(
            [
                _render_artifact(artifact)
                for artifact in self._artifact_store.discover_workspace_artifacts(
                    kinds, revision
                )
            ]
        )

    def _evaluate_expression(
        self,
        condition: StateCondition,
        expression: str,
        activation: Mapping[str, Any],
    ) -> Any:
        """Compile and evaluate one CEL expression, mapping runtime failure to state.

        Function 5, invariant 2: a CEL expression failing AT RUNTIME is
        `state-error` (`condition-evaluation-failed`), the detail naming the slug.
        """
        try:
            program = self._environment.program(self._environment.compile(expression))
            return program.evaluate(dict(activation))
        except celpy.CELEvalError as error:
            raise StateError(
                "condition-evaluation-failed",
                f"Condition '{condition.slug}' failed to evaluate "
                f"'{expression}': {error}",
                False,
            ) from error


__all__ = ["ConditionEvaluator"]
