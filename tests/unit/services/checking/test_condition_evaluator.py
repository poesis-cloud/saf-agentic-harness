"""Unit tests for `ConditionEvaluator` — the CEL machinery functions 5 and 10 share."""

from __future__ import annotations

from pathlib import Path

import pytest

from checking_fixtures import (
    INSTANCE_ID,
    ORCHESTRATOR_SESSION,
    build_entry,
    build_instance_view,
    build_step,
    build_step_resolution_entry,
    write_artifact,
)
from config import StateCondition, StepCondition
from errors import StateError
from services.checking import ConditionEvaluator
from stores.artifact_store import ArtifactStore

SELECT_DECLARED_ARTIFACT = "artifacts['review-report'].filter(a, a.slug == artifact)"


def _executed_view(step_slug: str):
    """Assemble an instance view in which `step_slug` is journaled executed."""
    return build_instance_view(
        [
            build_step_resolution_entry(ORCHESTRATOR_SESSION, build_step(step_slug)),
            build_entry(
                "check-step-postconditions",
                "pass",
                session_id=ORCHESTRATOR_SESSION,
                workflow_instance_id=INSTANCE_ID,
                payload={"conditionChecks": []},
                timestamp="2026-01-01T00:02:00Z",
            ),
        ]
    )


class TestConditionEvaluator:
    def test_passes_a_precondition_whose_predecessor_is_journaled_executed(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 1: for `kind: precondition` the referenced step must
        be journaled executed in the correlated workflow instance."""
        evaluator = ConditionEvaluator(artifact_store)
        condition = StepCondition(kind="precondition", slug="after-build", step="build")

        checks = evaluator.evaluate_conditions(
            (condition,), _executed_view("build"), "review-report"
        )

        assert [(check.condition.slug, check.outcome) for check in checks] == [
            ("after-build", "pass")
        ]
        assert checks[0].failure_message is None

    def test_fails_a_precondition_whose_predecessor_is_not_journaled_executed(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 1: a predecessor not journaled executed FAILS the
        condition — never a silent pass; the instance view always exists."""
        evaluator = ConditionEvaluator(artifact_store)
        condition = StepCondition(kind="precondition", slug="after-build", step="build")

        checks = evaluator.evaluate_conditions(
            (condition,), build_instance_view([]), "review-report"
        )

        assert checks[0].outcome == "fail"
        assert "build" in checks[0].failure_message

    def test_never_gates_on_an_advisory_postcondition_step_condition(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 1: a `kind: postcondition` stepCondition naming a
        successor is advisory — never itself re-checked as a hard gate; the DAG edge
        is enforced once, from the successor's side."""
        evaluator = ConditionEvaluator(artifact_store)
        condition = StepCondition(kind="postcondition", slug="unblocks-ship", step="ship")

        checks = evaluator.evaluate_conditions(
            (condition,), build_instance_view([]), "review-report"
        )

        assert checks[0].outcome == "pass"

    def test_passes_a_state_condition_whose_predicate_holds_over_the_selected_set(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """Function 5, invariant 2: `setSelector.setQuery` references artifacts by
        schema slug via the `artifacts` runtime constant to produce `selected`;
        `setPredicate` is then evaluated over `selected`."""
        write_artifact(
            workspace, "review-report/r1.json", {"slug": "review-report", "status": "draft"}
        )
        evaluator = ConditionEvaluator(artifact_store)
        condition = StateCondition(
            kind="precondition",
            slug="report-exists",
            set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
            set_predicate="selected.size() == 1",
        )

        checks = evaluator.evaluate_conditions(
            (condition,), build_instance_view([]), "review-report"
        )

        assert checks[0].outcome == "pass"

    def test_scopes_the_selector_to_the_steps_declared_artifact_constant(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """Function 5, invariant 2: the step's declared `artifact` ref is in scope as
        a runtime constant — the selector filters to the step's declared artifact."""
        write_artifact(
            workspace, "review-report/r1.json", {"slug": "other-report", "status": "draft"}
        )
        evaluator = ConditionEvaluator(artifact_store)
        condition = StateCondition(
            kind="precondition",
            slug="report-exists",
            set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
            set_predicate="selected.size() == 1",
        )

        checks = evaluator.evaluate_conditions(
            (condition,), build_instance_view([]), "review-report"
        )

        assert checks[0].outcome == "fail"

    def test_treats_a_false_predicate_as_a_fail_check_not_an_error(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: an empty selected set is a normal value — the
        predicate decides; a false predicate is NEVER an error, it is a `fail` check
        inside a normal report."""
        evaluator = ConditionEvaluator(artifact_store)
        condition = StateCondition(
            kind="precondition",
            slug="report-exists",
            set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
            set_predicate="selected.size() == 1",
        )

        checks = evaluator.evaluate_conditions(
            (condition,), build_instance_view([]), "review-report"
        )

        assert checks[0].outcome == "fail"
        assert checks[0].failure_message

    def test_passes_a_predicate_that_accepts_an_empty_selected_set(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: an empty selected set is a normal value — the
        predicate decides whether it passes or fails."""
        evaluator = ConditionEvaluator(artifact_store)
        condition = StateCondition(
            kind="precondition",
            slug="nothing-pending",
            set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
            set_predicate="selected.size() == 0",
        )

        checks = evaluator.evaluate_conditions(
            (condition,), build_instance_view([]), "review-report"
        )

        assert checks[0].outcome == "pass"

    def test_asserts_committed_state_only_never_the_working_tree(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """Function 10, invariant 1 and C1: state assertions evaluate over PERSISTED
        artifacts only — committed state is workspace state (Git plane, principle 1)."""
        write_artifact(
            workspace,
            "review-report/r1.json",
            {"slug": "review-report", "status": "draft"},
            commit=False,
        )
        evaluator = ConditionEvaluator(artifact_store)
        condition = StateCondition(
            kind="postcondition",
            slug="report-exists",
            set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
            set_predicate="selected.size() == 1",
        )

        checks = evaluator.evaluate_conditions(
            (condition,), build_instance_view([]), "review-report"
        )

        assert checks[0].outcome == "fail"

    def test_raises_condition_evaluation_failed_on_a_runtime_cel_failure(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: a CEL expression failing AT RUNTIME (an evaluation
        error, not a false predicate) is `state-error` (`condition-evaluation-failed`),
        the error detail naming the condition slug."""
        evaluator = ConditionEvaluator(artifact_store)
        condition = StateCondition(
            kind="precondition",
            slug="report-exists",
            set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
            set_predicate="selected[7].slug == 'x'",
        )

        with pytest.raises(StateError) as raised:
            evaluator.evaluate_conditions(
                (condition,), build_instance_view([]), "review-report"
            )

        assert raised.value.code == "condition-evaluation-failed"
        assert "report-exists" in raised.value.message

    def test_raises_condition_evaluation_failed_on_a_non_boolean_predicate(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: `setPredicate` is a CEL boolean over `selected` —
        a predicate that yields no boolean failed at runtime, not a false predicate."""
        evaluator = ConditionEvaluator(artifact_store)
        condition = StateCondition(
            kind="precondition",
            slug="report-count",
            set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
            set_predicate="selected.size()",
        )

        with pytest.raises(StateError) as raised:
            evaluator.evaluate_conditions(
                (condition,), build_instance_view([]), "review-report"
            )

        assert raised.value.code == "condition-evaluation-failed"

    def test_returns_one_check_per_condition_keyed_by_its_unique_slug(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 3: condition slugs are the audit handle — unique
        within a step, and every check logs the full condition object under that slug."""
        evaluator = ConditionEvaluator(artifact_store)
        conditions = (
            StepCondition(kind="precondition", slug="after-build", step="build"),
            StepCondition(kind="precondition", slug="after-design", step="design"),
        )

        checks = evaluator.evaluate_conditions(
            conditions, _executed_view("build"), "review-report"
        )

        assert [check.condition.slug for check in checks] == ["after-build", "after-design"]
        assert [check.outcome for check in checks] == ["pass", "fail"]

    def test_returns_no_checks_for_an_empty_condition_list(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 4: a step declaring zero preconditions passes
        vacuously — an empty `conditionChecks` array."""
        evaluator = ConditionEvaluator(artifact_store)

        assert evaluator.evaluate_conditions((), build_instance_view([]), "review-report") == ()
