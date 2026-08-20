"""The CEL machinery functions 5 and 10 share, over persisted workspace state."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import celpy
from celpy import celtypes

from config import StateCondition, StepCondition
from errors import StateError
from services.checking.condition_check import ConditionCheck
from stores.artifact_store import ArtifactStore
from stores.session_log_store import WorkflowInstanceView

_ARTIFACTS_REFERENCE = re.compile(r"artifacts\[\s*['\"]([a-z0-9-]+)['\"]\s*\]")
_PASS = "pass"
_FAIL = "fail"


def _thaw(value: Any) -> Any:
    """Convert frozen artifact data into the plain containers `json_to_cel` admits."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


class ConditionEvaluator:
    """Evaluate a step's declared conditions against persisted workspace state.

    Spec (C1, C2): conditions are evaluated strictly against persisted workspace
    state — committed artifacts and the instance view of the logs — never against
    anything an agent merely remembers.
    """

    def __init__(self, artifact_store: ArtifactStore) -> None:
        """Create the evaluator over the artifact plane it asserts."""
        self._artifact_store = artifact_store
        self._environment = celpy.Environment()

    def evaluate_conditions(
        self,
        conditions: Sequence[StepCondition | StateCondition],
        view: WorkflowInstanceView,
        artifact: str,
    ) -> tuple[ConditionCheck, ...]:
        """Evaluate every condition in order, one check per declared condition.

        `artifact` is the step's declared artifact ref, in scope as a CEL runtime
        constant (function 5, invariant 2).
        """
        return tuple(
            self._evaluate_condition(condition, view, artifact)
            for condition in conditions
        )

    def _evaluate_condition(
        self,
        condition: StepCondition | StateCondition,
        view: WorkflowInstanceView,
        artifact: str,
    ) -> ConditionCheck:
        """Evaluate one condition into its check."""
        if isinstance(condition, StepCondition):
            return self._evaluate_step_condition(condition, view)
        return self._evaluate_state_condition(condition, artifact)

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
        self, condition: StateCondition, artifact: str
    ) -> ConditionCheck:
        """Select persisted artifacts, then decide the predicate over `selected`."""
        set_query = condition.set_selector["setQuery"]
        activation: dict[str, Any] = {
            "artifacts": self._load_artifacts(set_query),
            "artifact": celtypes.StringType(artifact),
        }
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

    def _load_artifacts(self, set_query: str) -> celtypes.Value:
        """Load the committed instances of every artifact slug the query references."""
        slugs = sorted(set(_ARTIFACTS_REFERENCE.findall(set_query)))
        return celpy.json_to_cel(
            {
                slug: [
                    _thaw(entry.data)
                    for entry in self._artifact_store.discover_artifacts(slug)
                ]
                for slug in slugs
            }
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
