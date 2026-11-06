"""Unit tests for `ConditionEvaluator` — the condition language functions 5 and 10 share."""

from __future__ import annotations

from pathlib import Path

import pytest

from checking_fixtures import (
    INSTANCE_ID,
    ORCHESTRATOR_SESSION,
    build_entry,
    build_instance_view,
    build_scope,
    build_step,
    build_step_resolution_entry,
    run_git,
    write_artifact,
)
from config import StateCondition, StepCondition
from errors import StateError
from services.checking import ConditionEvaluator
from stores.artifact_store import ArtifactStore

# Function 10, invariant 5: every concluded step outcome carries its delivery verdict.
DELIVERED = {
    "artifact": "review-report",
    "artifactPath": "report/delivered.json",
    "outcome": "pass",
}

SELECT_REVIEW_REPORTS = "workspace.filter(a, a.kind == 'review-report')"
REPORT_REF = "review-report/r1.json"


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
                payload={
                    "conditionChecks": [],
                    "artifactDelivery": DELIVERED,
                    "promotion": {"outcome": "promoted", "workspaceCommit": "0" * 40},
                },
                timestamp="2026-01-01T00:02:00Z",
            ),
        ]
    )


def _state(set_query: str, set_predicate: str, **extra) -> StateCondition:
    """Build one state condition under the `report-exists` slug."""
    return StateCondition(
        kind=extra.pop("kind", "precondition"),
        slug=extra.pop("slug", "report-exists"),
        set_selector={"setQuery": set_query},
        set_predicate=set_predicate,
        **extra,
    )


def _evaluate(artifact_store: ArtifactStore, *conditions, view=None, scope=None):
    """Evaluate conditions over an empty instance view and the default scope."""
    return ConditionEvaluator(artifact_store).evaluate_conditions(
        conditions,
        view if view is not None else build_instance_view([]),
        scope if scope is not None else build_scope(),
    )


class TestStepConditions:
    def test_passes_a_precondition_whose_predecessor_is_journaled_executed(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 1: for `kind: precondition` the referenced step must
        be journaled executed in the correlated workflow instance."""
        condition = StepCondition(kind="precondition", slug="after-build", step="build")

        checks = _evaluate(artifact_store, condition, view=_executed_view("build"))

        assert [(check.condition.slug, check.outcome) for check in checks] == [
            ("after-build", "pass")
        ]
        assert checks[0].failure_message is None

    def test_fails_a_precondition_whose_predecessor_is_not_journaled_executed(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 1: a predecessor not journaled executed FAILS the
        condition — never a silent pass; the instance view always exists."""
        condition = StepCondition(kind="precondition", slug="after-build", step="build")

        checks = _evaluate(artifact_store, condition)

        assert checks[0].outcome == "fail"
        assert "build" in checks[0].failure_message

    def test_never_gates_on_an_advisory_postcondition_step_condition(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 1: a `kind: postcondition` stepCondition naming a
        successor is advisory — never itself re-checked as a hard gate."""
        condition = StepCondition(kind="postcondition", slug="unblocks-ship", step="ship")

        assert _evaluate(artifact_store, condition)[0].outcome == "pass"


class TestWorkspace:
    def test_selects_artifact_instances_by_a_kind_guard(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """Function 5, invariant 2: `workspace` holds the artifact instances of the
        evaluated state — `kind`, `path`, `data` — and `setPredicate` decides over
        `selected`, the result of `setQuery`."""
        write_artifact(workspace, REPORT_REF, {"slug": "review-report", "status": "draft"})
        condition = _state(
            SELECT_REVIEW_REPORTS,
            "selected.size() == 1 && selected.all(a, a.path == 'review-report/r1.json'"
            " && a.data.status == 'draft')",
        )

        assert _evaluate(artifact_store, condition)[0].outcome == "pass"

    def test_joins_the_current_steps_artifact_by_path(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """Function 5, invariant 2: in a step's own postconditions `step.artifact` is
        the artifact instance about to be promoted — joined to `workspace` by path."""
        write_artifact(workspace, REPORT_REF, {"slug": "review-report", "status": "approved"})
        write_artifact(workspace, "review-report/r2.json", {"slug": "other", "status": "draft"})
        scope = build_scope(
            step={
                "slug": "review",
                "id": "worker-1",
                "workflow": INSTANCE_ID,
                "actor": "qa-engineer",
                "outcome": "in-flight",
                "artifact": {"kind": "review-report", "path": REPORT_REF},
            }
        )
        condition = _state(
            "workspace.filter(a, a.path == step.artifact.path && a.data.status == 'approved')",
            "selected.size() == 1",
            kind="postcondition",
        )

        assert _evaluate(artifact_store, condition, scope=scope)[0].outcome == "pass"

    def test_step_artifact_kind_always_gives_the_declared_kind(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """Function 5, invariant 2: `step.artifact` is always `{kind: <declared kind>,
        path}` — in a precondition its path is null, its kind still the declared one."""
        write_artifact(workspace, REPORT_REF, {"slug": "review-report", "status": "draft"})
        condition = _state(
            "workspace.filter(a, a.kind == step.artifact.kind)",
            "selected.size() == 1 && step.artifact.path == null && step.id == null",
        )

        assert _evaluate(artifact_store, condition)[0].outcome == "pass"

    def test_reads_the_evaluated_revision_never_the_working_tree(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """C1; function 10, invariant 1: state assertions read committed state only —
        workspace state, or the step state's revision — never the working tree."""
        write_artifact(
            workspace, REPORT_REF, {"slug": "review-report", "status": "draft"}, commit=False
        )
        condition = _state(SELECT_REVIEW_REPORTS, "selected.size() == 1")
        assert _evaluate(artifact_store, condition)[0].outcome == "fail"

        run_git(workspace, "add", "--", REPORT_REF)
        run_git(workspace, "stash")
        step_state = run_git(workspace, "rev-parse", "stash@{0}").strip()

        checks = _evaluate(artifact_store, condition, scope=build_scope(step_state))

        assert checks[0].outcome == "pass"

    def test_loads_only_the_kinds_the_guards_reference(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """Function 5, invariant 2 + the load's narrowing: when the guards fix the
        kinds `workspace` can yield, only those kinds are read."""
        write_artifact(workspace, REPORT_REF, {"slug": "review-report", "status": "draft"})
        narrowed = _state("workspace", "selected.size() == 0", workspace_kinds=frozenset())
        every_kind = _state("workspace", "selected.size() == 1", slug="every-kind")

        checks = _evaluate(artifact_store, narrowed, every_kind)

        assert [check.outcome for check in checks] == ["pass", "pass"]


class TestLogPlane:
    def test_reads_steps_workflows_and_the_current_workflow(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: `steps` and `workflows` are the instances the logs
        record, `workflow` the current one — log evidence asserted as state (C5)."""
        scope = build_scope(
            steps=(
                {
                    "slug": "build",
                    "id": "worker-1",
                    "workflow": INSTANCE_ID,
                    "actor": "builder",
                    "outcome": "pass",
                    "artifact": {"kind": "build-report", "path": "build/b.json"},
                },
                {
                    "slug": "build",
                    "id": "worker-0",
                    "workflow": "other-01CD",
                    "actor": "builder",
                    "outcome": "fail",
                    "artifact": None,
                },
            ),
        )
        condition = _state(
            "steps.filter(s, s.workflow == workflow.id && s.slug == 'build' && s.outcome == 'pass')",
            "selected.size() == 1 && selected[0].artifact.path == 'build/b.json'"
            " && workflows.exists(w, w.id == workflow.id && w.slug == 'verification')",
        )

        assert _evaluate(artifact_store, condition, scope=scope)[0].outcome == "pass"


class TestOutcomes:
    def test_treats_a_false_predicate_as_a_fail_check_not_an_error(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: an empty `selected` is a normal value — a false
        predicate is NEVER an error, it is a `fail` check inside a normal report."""
        checks = _evaluate(artifact_store, _state(SELECT_REVIEW_REPORTS, "selected.size() == 1"))

        assert checks[0].outcome == "fail"
        assert checks[0].failure_message

    def test_passes_a_predicate_that_accepts_an_empty_selected_set(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: the predicate decides whether an empty set passes."""
        condition = _state(SELECT_REVIEW_REPORTS, "selected.size() == 0", slug="nothing-pending")

        assert _evaluate(artifact_store, condition)[0].outcome == "pass"

    def test_raises_condition_evaluation_failed_on_a_runtime_cel_failure(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: a CEL expression failing AT RUNTIME is
        `state-error` (`condition-evaluation-failed`), the detail naming the slug."""
        condition = _state(SELECT_REVIEW_REPORTS, "selected[7].path == 'x'")

        with pytest.raises(StateError) as raised:
            _evaluate(artifact_store, condition)

        assert raised.value.code == "condition-evaluation-failed"
        assert "report-exists" in raised.value.message

    def test_raises_condition_evaluation_failed_on_a_non_boolean_predicate(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: a predicate that yields no boolean failed at
        runtime — it is not a false predicate."""
        condition = _state(SELECT_REVIEW_REPORTS, "selected.size()", slug="report-count")

        with pytest.raises(StateError) as raised:
            _evaluate(artifact_store, condition)

        assert raised.value.code == "condition-evaluation-failed"

    def test_returns_one_check_per_condition_keyed_by_its_unique_slug(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 3: condition slugs are the audit handle."""
        conditions = (
            StepCondition(kind="precondition", slug="after-build", step="build"),
            StepCondition(kind="precondition", slug="after-design", step="design"),
        )

        checks = _evaluate(artifact_store, *conditions, view=_executed_view("build"))

        assert [check.condition.slug for check in checks] == ["after-build", "after-design"]
        assert [check.outcome for check in checks] == ["pass", "fail"]

    def test_returns_no_checks_for_an_empty_condition_list(
        self, artifact_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 4: zero preconditions pass vacuously."""
        assert _evaluate(artifact_store) == ()
