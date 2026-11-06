"""Unit tests for `StepPostconditionChecker` — function 10, `check-step-postconditions`.

The step instance's work is real: function 0's step ref and function 9's commits
are made through the real `ArtifactStore` in a tmp-dir Git workspace, and the
function-9 entries the worker session journals carry those commits' ids.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from checking_fixtures import (
    INSTANCE_ID,
    ORCHESTRATOR_SESSION,
    STEP_SESSION,
    build_catalog,
    build_ending_entry,
    build_entry,
    build_registration_entry,
    build_step,
    build_step_resolution_entry,
    read_entries,
    run_git,
    write_artifact,
)
from config import StateCondition, StepCondition, WorkflowCatalog, WorkspaceLayout
from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from errors import SystemFailureError
from services.checking import (
    CheckStepPostconditionsReport,
    ConditionEvaluator,
    StepPostconditionChecker,
)
from stores.artifact_store import ArtifactStore, StepRef
from stores.session_log_store import SessionLogStore

DELIVERED_REF = "review-report/r1.json"
SELECT_DELIVERED = "workspace.filter(a, a.path == step.artifact.path)"
APPROVED = {"slug": "review-report", "status": "approved"}


class _FailingSessionLogStore(SessionLogStore):
    """A fake store whose reads fail the way an unreadable environment does."""

    def load_session_log(self, session_id: str):
        """Fail the read exactly as an unreadable log does (rule 1, `system-error`)."""
        raise SystemFailureError("log-unreadable", "Session log is unreadable.", True)


class _Rig:
    """One orchestrator session, its in-flight `review` step, and its worker session."""

    def __init__(
        self, workspace: Path, artifact_store: ArtifactStore, log_store: SessionLogStore
    ) -> None:
        self.workspace = workspace
        self.artifact_store = artifact_store
        self.log_store = log_store

    def open_session(self, *, with_step: bool = True, worker: bool = True) -> None:
        """Register the orchestrator session, put `review` in flight, and register its worker."""
        self.log_store.create_session_log(
            build_registration_entry(ORCHESTRATOR_SESSION, "orchestrator")
        )
        if with_step:
            self.log_store.append_log_entry(
                ORCHESTRATOR_SESSION,
                build_step_resolution_entry(ORCHESTRATOR_SESSION, build_step()),
            )
            if worker:
                self.register_worker()

    def register_worker(
        self, session_id: str = STEP_SESSION, *, timestamp: str = "2026-01-01T00:01:30Z"
    ) -> StepRef:
        """Register the worker session and create its step ref, as function 0 does."""
        self.log_store.create_session_log(
            build_registration_entry(
                session_id,
                "qa-engineer",
                parent_session_id=ORCHESTRATOR_SESSION,
                timestamp=timestamp,
            )
        )
        return self.artifact_store.create_step_ref(INSTANCE_ID, "review", session_id)

    def deliver(
        self,
        *refs: str,
        data: dict | None = None,
        session_id: str = STEP_SESSION,
        journal: bool = True,
        commit_session: str | None = None,
    ) -> None:
        """Write, commit onto the worker's step ref (function 9), and journal the commit."""
        for ref in refs:
            path = self.workspace / ref
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data or APPROVED), encoding="utf-8")
        step_ref = self.artifact_store.find_step_ref(session_id)
        assert step_ref is not None
        commit = self.artifact_store.commit_artifacts(
            tuple(Path(ref) for ref in refs),
            session_id=commit_session or session_id,
            step_ref=step_ref,
        )
        if journal:
            self.log_store.append_log_entry(
                session_id,
                build_entry(
                    "check-step-artifact",
                    "valid",
                    session_id=session_id,
                    parent_session_id=ORCHESTRATOR_SESSION,
                    payload={
                        "commitId": None if commit is None else commit.commit_id,
                        "committedPaths": [] if commit is None else list(commit.paths),
                    },
                    timestamp="2026-01-01T00:01:45Z",
                ),
            )

    def head(self) -> str:
        return run_git(self.workspace, "rev-parse", "HEAD").strip()

    def step_ref_tip(self, session_id: str = STEP_SESSION) -> str:
        step_ref = self.artifact_store.find_step_ref(session_id)
        assert step_ref is not None
        return step_ref.tip


@pytest.fixture()
def rig(workspace: Path, artifact_store: ArtifactStore, log_store: SessionLogStore) -> _Rig:
    return _Rig(workspace, artifact_store, log_store)


def _build_checker(
    artifact_store: ArtifactStore,
    log_store: SessionLogStore,
    catalog: WorkflowCatalog,
) -> StepPostconditionChecker:
    """Wire the checker with its constructor-injected collaborators."""
    return StepPostconditionChecker(
        ConditionEvaluator(artifact_store), log_store, catalog, artifact_store
    )


def _check(rig: _Rig, catalog: WorkflowCatalog | None = None, store: ArtifactStore | None = None):
    checker = _build_checker(store or rig.artifact_store, rig.log_store, catalog or build_catalog())
    return checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)


def _postcondition(set_query: str, set_predicate: str, slug: str = "report-approved") -> StateCondition:
    return StateCondition(
        kind="postcondition",
        slug=slug,
        set_selector={"setQuery": set_query},
        set_predicate=set_predicate,
    )


class TestStepPostconditionChecker:
    def test_answers_not_applicable_when_no_step_is_in_flight(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Function 10, precondition (E): an in-flight step exists — violation:
        `not-applicable` (rule 2)."""
        rig.open_session(with_step=False)

        report = _check(rig)

        assert report.outcome.status == "not-applicable"
        assert len(read_entries(workspace, ORCHESTRATOR_SESSION)) == 1

    def test_absorbs_a_duplicate_step_ended_delivery_as_not_applicable(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Function 10, precondition (E) and invariant 2: after the first outcome
        journals, re-delivery finds no target."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)

        first = _check(rig)
        second = _check(rig)

        assert (first.outcome.status, second.outcome.status) == ("pass", "not-applicable")
        journaled = [
            entry["report"]["context"]["function"]
            for entry in read_entries(workspace, ORCHESTRATOR_SESSION)
        ]
        assert journaled.count("check-step-postconditions") == 1

    def test_reports_state_error_when_the_in_flight_resolution_names_no_instance(
        self, rig: _Rig, log_store: SessionLogStore, workspace: Path
    ) -> None:
        """Rule 1: `step-correlation-missing` is `state-error`, journaled."""
        log_store.create_session_log(
            build_registration_entry(ORCHESTRATOR_SESSION, "orchestrator")
        )
        log_store.append_log_entry(
            ORCHESTRATOR_SESSION,
            build_entry(
                "resolve-step",
                "step-resolution",
                session_id=ORCHESTRATOR_SESSION,
                payload={"step": build_step()},
                timestamp="2026-01-01T00:01:00Z",
            ),
        )

        report = _check(rig)

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "step-correlation-missing"
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)
        assert journaled[-1]["report"]["outcome"]["status"] == "state-error"

    def test_reports_one_check_per_declared_postcondition_with_an_aggregate_pass(
        self, rig: _Rig
    ) -> None:
        """Function 10, worked example: `outcome: pass` with one `conditionChecks`
        entry for the declared postcondition, joined to the delivery by path."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        catalog = build_catalog(
            conditions=(
                _postcondition(
                    "workspace.filter(a, a.path == step.artifact.path && a.data.status == 'approved')",
                    "selected.size() == 1",
                ),
            )
        )

        report = _check(rig, catalog)

        assert report.outcome.status == "pass"
        assert [
            (check.condition.slug, check.outcome) for check in report.condition_checks
        ] == [("report-approved", "pass")]

    def test_ignores_declared_preconditions(self, rig: _Rig) -> None:
        """Function 10, Summary: the same machinery, applied to the POSTCONDITIONS."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        catalog = build_catalog(
            conditions=(
                StepCondition(kind="precondition", slug="after-build", step="build"),
                StepCondition(kind="postcondition", slug="unblocks-ship", step="ship"),
            )
        )

        report = _check(rig, catalog)

        assert [check.condition.slug for check in report.condition_checks] == [
            "unblocks-ship"
        ]

    def test_evaluates_over_the_step_state_never_the_working_tree(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Function 10, invariant 1 / C1: postconditions read the step state — the
        step instance's committed work — never the working tree, which may hold later,
        uncommitted bytes."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        (workspace / DELIVERED_REF).write_text(
            json.dumps({"slug": "review-report", "status": "draft"}), encoding="utf-8"
        )
        catalog = build_catalog(
            conditions=(
                _postcondition(
                    "workspace.filter(a, a.path == step.artifact.path)",
                    "selected.all(a, a.data.status == 'approved')",
                ),
            )
        )

        assert _check(rig, catalog).outcome.status == "pass"

    def test_lists_the_current_step_instance_in_flight_with_its_delivery(
        self, rig: _Rig
    ) -> None:
        """Function 5, invariant 2 / function 10, invariant 1: in a step's own
        postconditions `steps` includes the current step instance, `outcome:
        'in-flight'`, its `artifact` the delivery being evaluated — and `step` is it."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        catalog = build_catalog(
            conditions=(
                _postcondition(
                    "steps.filter(s, s.id == step.id)",
                    "selected.size() == 1 && selected[0].outcome == 'in-flight'"
                    f" && selected[0].artifact.path == '{DELIVERED_REF}'"
                    f" && step.id == '{STEP_SESSION}' && step.slug == 'review'"
                    " && step.artifact.kind == 'review-report'",
                    slug="current-step",
                ),
            )
        )

        report = _check(rig, catalog)

        assert report.outcome.status == "pass", report.condition_checks

    def test_journals_the_step_outcome_function_3s_cursor_reads(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Function 10, invariant 3: this journaled outcome is what the cursor reads."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)

        _check(rig)

        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["context"]["function"] == "check-step-postconditions"
        assert journaled["context"]["sessionId"] == ORCHESTRATOR_SESSION
        assert journaled["context"]["workflowInstanceId"] == INSTANCE_ID
        assert journaled["outcome"]["status"] == "pass"

    def test_passes_vacuously_and_journals_when_zero_postconditions_are_declared(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Function 10, invariant 4: declared postconditions may be zero."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)

        report = _check(rig)

        assert (report.outcome.status, report.condition_checks) == ("pass", ())
        assert read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"][
            "conditionChecks"
        ] == []

    def test_refuses_an_ended_session_without_journaling(
        self, rig: _Rig, log_store: SessionLogStore, workspace: Path
    ) -> None:
        """C8 / rule 3: `session-ended`, never journaled."""
        rig.open_session()
        log_store.append_log_entry(
            ORCHESTRATOR_SESSION, build_ending_entry(ORCHESTRATOR_SESSION)
        )

        report = _check(rig)

        assert (report.outcome.status, report.outcome.error.code) == (
            "state-error",
            "session-ended",
        )
        assert read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]["context"][
            "function"
        ] == "end-session"

    def test_reports_an_unregistered_session_as_an_inquiry_error_without_a_log(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Rule 1 / rule 4: `session-unregistered`, no log to journal to."""
        checker = _build_checker(rig.artifact_store, rig.log_store, build_catalog())

        report = checker.check_step_postconditions("01jmissing", None)

        assert (report.outcome.status, report.outcome.error.code) == (
            "inquiry-error",
            "session-unregistered",
        )
        assert read_entries(workspace, "01jmissing") == []

    def test_reports_a_runtime_condition_failure_as_a_journaled_state_error(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Function 10, invariant 1: a runtime CEL failure is `state-error`
        (`condition-evaluation-failed`), journaled; like any error, it withholds."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        head = rig.head()
        catalog = build_catalog(
            conditions=(_postcondition(SELECT_DELIVERED, "selected[7].path == 'x'"),)
        )

        report = _check(rig, catalog)

        assert report.outcome.error.code == "condition-evaluation-failed"
        assert read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]["outcome"][
            "error"
        ]["code"] == "condition-evaluation-failed"
        assert rig.head() == head

    def test_reports_an_unreadable_environment_as_a_system_error(
        self, artifact_store: ArtifactStore, workspace: Path
    ) -> None:
        """Rule 1: the environment failing is the uniform `system-error`."""
        checker = _build_checker(
            artifact_store, _FailingSessionLogStore(workspace), build_catalog()
        )

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "system-error"

    def test_returns_a_report_bound_to_its_own_output_contract(self, rig: _Rig) -> None:
        """Classes: which function produced a report is read from `context.function`."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)

        report = _check(rig)

        assert isinstance(report, CheckStepPostconditionsReport)
        assert report.context.function == "check-step-postconditions"


class TestPromotion:
    """Function 10, invariants 6–7 and the order of operations: the acceptance point."""

    def test_a_passing_step_instance_is_promoted_to_exactly_the_evaluated_state(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Step 3: ONE compare-and-swap fast-forward to the step ref tip; step 4: the
        promoted paths' index entries synced; the working tree is never rewritten."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        tip = rig.step_ref_tip()

        report = _check(rig)

        assert report.outcome.status == "pass"
        assert (report.promotion.outcome, report.promotion.workspace_commit) == (
            "promoted",
            tip,
        )
        assert rig.head() == tip
        assert run_git(workspace, "status", "--porcelain", "--", "review-report") == ""
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["promotion"] == {"outcome": "promoted", "workspaceCommit": tip}

    def test_a_failing_step_instance_is_withheld_with_the_branch_untouched(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Step 5: on `fail`, no promotion, no index sync, no lock act — the step ref
        is kept for the retry to build on."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        step_ref = rig.artifact_store.find_step_ref(STEP_SESSION)
        rig.artifact_store.claim_artifact_lock(DELIVERED_REF, step_ref, None)
        head, tip = rig.head(), rig.step_ref_tip()
        catalog = build_catalog(
            conditions=(_postcondition(SELECT_DELIVERED, "selected.size() == 2"),)
        )

        report = _check(rig, catalog)

        assert report.outcome.status == "fail"
        assert (report.promotion.outcome, report.promotion.workspace_commit) == (
            "withheld",
            head,
        )
        assert rig.head() == head
        assert rig.step_ref_tip() == tip
        assert rig.artifact_store.find_artifact_lock(DELIVERED_REF).holder == step_ref.name

    def test_a_promotion_releases_the_step_instances_locks(self, rig: _Rig) -> None:
        """Step 4: after the acceptance point, the step instance's locks are deleted."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        step_ref = rig.artifact_store.find_step_ref(STEP_SESSION)
        rig.artifact_store.claim_artifact_lock(DELIVERED_REF, step_ref, None)

        _check(rig)

        assert rig.artifact_store.find_artifact_lock(DELIVERED_REF) is None

    def test_a_moved_branch_is_rebased_over_then_promoted(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Step 1: if the workspace branch moved since the step ref's base, the step
        instance's commits are rebased in memory onto its tip, then promoted — history
        linear, the foreign commit kept."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        write_artifact(workspace, "review-report/foreign.json", APPROVED)
        moved = rig.head()

        report = _check(rig)

        assert report.promotion.outcome == "promoted"
        assert run_git(workspace, "merge-base", "--is-ancestor", moved, "HEAD") == ""
        assert run_git(workspace, "rev-list", "--count", f"{moved}..HEAD").strip() == "1"
        assert rig.step_ref_tip() == rig.head()

    def test_a_path_changed_on_both_sides_is_a_journaled_rebase_conflict(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Step 1: a path changed both by the step ref and by the commits it is rebased
        over is `state-error` (`step-rebase-conflict`), journaled: the step instance
        concludes not executed, the branch and the step ref untouched."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        tip = rig.step_ref_tip()
        write_artifact(workspace, DELIVERED_REF, {"slug": "review-report", "status": "draft"})
        moved = rig.head()

        report = _check(rig)

        assert (report.outcome.status, report.outcome.error.code) == (
            "state-error",
            "step-rebase-conflict",
        )
        assert rig.head() == moved
        assert rig.step_ref_tip() == tip
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["outcome"]["error"]["code"] == "step-rebase-conflict"

    def test_a_failed_compare_and_swap_is_a_rebase_conflict(
        self,
        rig: _Rig,
        workspace: Path,
        artifact_store: ArtifactStore,
        artifact_schema: Path,
    ) -> None:
        """Step 3: if the branch moved between the rebase and the update, the CAS fails
        — `step-rebase-conflict`, the branch and the locks untouched."""

        class _RacingStore(ArtifactStore):
            def promote_step_ref(self, step_ref: StepRef, expected_head: str) -> str:
                write_artifact(workspace, "review-report/racer.json", APPROVED)
                return super().promote_step_ref(step_ref, expected_head)

        racing = _RacingStore(
            workspace,
            {"review-report": artifact_schema},
            artifact_store._workspace_layout,
        )
        rig.open_session()
        rig.deliver(DELIVERED_REF)
        step_ref = racing.find_step_ref(STEP_SESSION)
        racing.claim_artifact_lock(DELIVERED_REF, step_ref, None)

        report = _check(rig, store=racing)

        assert report.outcome.error.code == "step-rebase-conflict"
        assert "racer" in run_git(workspace, "log", "-1", "--format=%s")
        assert racing.find_artifact_lock(DELIVERED_REF).holder == step_ref.name


class TestArtifactDelivery:
    """Function 10, invariants 4–5: delivery is never optional."""

    def test_one_committed_instance_delivers_and_journals_in_the_same_entry(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Invariant 5: the step state changes exactly one instance of the declared
        kind, and the worker session's own commits change it."""
        rig.open_session()
        rig.deliver(DELIVERED_REF)

        report = _check(rig)

        assert report.outcome.status == "pass"
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["artifactDelivery"] == {
            "artifact": "review-report",
            "artifactPath": DELIVERED_REF,
            "outcome": "pass",
        }

    def test_zero_postconditions_and_no_commit_fails(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Invariant 4: a worker session that committed nothing did not deliver."""
        rig.open_session()

        report = _check(rig)

        assert (report.outcome.status, report.condition_checks) == ("fail", ())
        delivery = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"][
            "artifactDelivery"
        ]
        assert (delivery["outcome"], delivery["artifactPath"]) == ("fail", None)
        assert "review" in delivery["failureMessage"]
        assert "review-report" in delivery["failureMessage"]

    def test_no_registered_worker_session_fails(self, rig: _Rig) -> None:
        """Invariant 5 / the no-step-ref bound: nothing to promote, withheld."""
        rig.open_session(worker=False)

        report = _check(rig)

        assert report.outcome.status == "fail"
        assert report.artifact_delivery.outcome == "fail"
        assert report.promotion.outcome == "withheld"

    def test_an_instance_already_in_workspace_state_does_not_deliver(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Invariant 5 (a): an instance already in `HEAD` is no change of the step state."""
        write_artifact(workspace, DELIVERED_REF, APPROVED)
        rig.open_session()

        report = _check(rig)

        assert report.outcome.status == "fail"
        assert report.artifact_delivery.artifact_path is None

    def test_several_commits_to_one_instance_deliver_it_once(self, rig: _Rig) -> None:
        """Invariant 5: creating then updating the same instance is one instance."""
        rig.open_session()
        rig.deliver(DELIVERED_REF, data={"slug": "review-report", "status": "draft"})
        rig.deliver(DELIVERED_REF)

        report = _check(rig)

        assert report.outcome.status == "pass"
        assert report.artifact_delivery.artifact_path == DELIVERED_REF

    def test_more_than_one_instance_fails(self, rig: _Rig) -> None:
        """Invariant 5: exactly one instance — two fail, and the message says so."""
        rig.open_session()
        rig.deliver(DELIVERED_REF, "review-report/r2.json")

        report = _check(rig)

        delivery = report.artifact_delivery
        assert report.outcome.status == "fail"
        assert (delivery.outcome, delivery.artifact_path) == ("fail", None)
        assert "2" in delivery.failure_message

    def test_the_worker_sessions_own_commits_must_change_the_instance(
        self, rig: _Rig
    ) -> None:
        """Invariant 5 (b): a step state that changes the instance only through
        commits the worker session did not make — a retry's inherited work — does not
        deliver: the retry still records its decision in the artifact."""
        rig.open_session()
        rig.deliver(DELIVERED_REF, journal=False, commit_session="01jearlier")

        report = _check(rig)

        assert report.outcome.status == "fail"
        assert "own commits" in report.artifact_delivery.failure_message

    def test_a_reverted_write_does_not_deliver(self, rig: _Rig, log_store: SessionLogStore) -> None:
        """Invariant 5: only a commit through function 9 delivers."""
        rig.open_session()
        log_store.append_log_entry(
            STEP_SESSION,
            build_entry(
                "check-step-artifact",
                "reverted",
                session_id=STEP_SESSION,
                parent_session_id=ORCHESTRATOR_SESSION,
                payload={
                    "artifactChecks": [
                        {
                            "artifactPath": DELIVERED_REF,
                            "failureMessage": "invalid",
                            "revert": {"action": "deleted"},
                        }
                    ]
                },
                timestamp="2026-01-01T00:01:45Z",
            ),
        )

        assert _check(rig).outcome.status == "fail"

    def test_a_failed_delivery_fails_the_aggregate_though_every_condition_passes(
        self, rig: _Rig, workspace: Path
    ) -> None:
        """Invariant 4: passes only if delivered AND every postcondition passes."""
        rig.open_session()
        catalog = build_catalog(
            conditions=(
                StepCondition(kind="postcondition", slug="unblocks-ship", step="ship"),
            )
        )

        report = _check(rig, catalog)

        assert report.outcome.status == "fail"
        assert [check.outcome for check in report.condition_checks] == ["pass"]
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["outcome"]["status"] == "fail"


class TestOtherKinds:
    """Function 10, invariant 5: writes to other kinds are governed by function 8 only."""

    @pytest.fixture()
    def two_kind_store(self, workspace: Path, artifact_schema: Path) -> ArtifactStore:
        notes_schema = artifact_schema.with_name("notes.artifact.schema.json")
        notes_schema.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": "gsmarc://tests/contracts/notes-artifact/v1",
                    "$ref": "gsmarc://saf/contracts/artifact/v1",
                }
            ),
            encoding="utf-8",
        )
        layout = WorkspaceLayout(
            nodes=tuple(
                FolderNode(
                    slug=kind,
                    description=f"{kind} folder",
                    children=(
                        ArtifactNode(
                            slug="<name>.json",
                            description=f"One {kind}",
                            cardinality="0..*",
                            artifact=kind,
                            template=kind,
                        ),
                    ),
                )
                for kind in ("review-report", "notes")
            )
        )
        return ArtifactStore(
            workspace, {"review-report": artifact_schema, "notes": notes_schema}, layout
        )

    def test_writes_to_other_kinds_are_ignored(
        self, workspace: Path, two_kind_store: ArtifactStore, log_store: SessionLogStore
    ) -> None:
        """They neither deliver nor count as a second instance."""
        rig = _Rig(workspace, two_kind_store, log_store)
        rig.open_session()
        rig.deliver(DELIVERED_REF, "notes/n1.json")

        report = _check(rig)

        assert report.outcome.status == "pass"
        assert report.artifact_delivery.artifact_path == DELIVERED_REF

    def test_only_other_kinds_committed_fails(
        self, workspace: Path, two_kind_store: ArtifactStore, log_store: SessionLogStore
    ) -> None:
        """A commit of another kind is not the step's artifact."""
        rig = _Rig(workspace, two_kind_store, log_store)
        rig.open_session()
        rig.deliver("notes/n1.json")

        assert _check(rig).outcome.status == "fail"
