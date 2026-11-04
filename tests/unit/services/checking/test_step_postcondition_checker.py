"""Unit tests for `StepPostconditionChecker` — function 10, `check-step-postconditions`."""

from __future__ import annotations

from pathlib import Path

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
from config import (
    ARTIFACT_DELIVERED_SLUG,
    StateCondition,
    StepCondition,
    WorkflowCatalog,
)
from errors import SystemFailureError
from services.checking import (
    CheckStepPostconditionsReport,
    ConditionEvaluator,
    StepPostconditionChecker,
)
from stores.artifact_store import ArtifactStore
from stores.session_log_store import SessionLogStore

SELECT_DECLARED_ARTIFACT = "artifacts[artifact].filter(a, a.status == 'approved')"
DELIVERED_REF = "review-report/delivered.json"


class _FailingSessionLogStore(SessionLogStore):
    """A fake store whose reads fail the way an unreadable environment does."""

    def load_session_log(self, session_id: str):
        """Fail the read exactly as an unreadable log does (rule 1, `system-error`)."""
        raise SystemFailureError("log-unreadable", "Session log is unreadable.", True)


def _open_session(log_store: SessionLogStore, *, with_step: bool = True) -> None:
    """Register the orchestrator session and optionally put a step in flight."""
    log_store.create_session_log(
        build_registration_entry(ORCHESTRATOR_SESSION, "orchestrator")
    )
    if with_step:
        log_store.append_log_entry(
            ORCHESTRATOR_SESSION,
            build_step_resolution_entry(ORCHESTRATOR_SESSION, build_step()),
        )


def _open_step_session(
    log_store: SessionLogStore,
    session_id: str = STEP_SESSION,
    *,
    agent: str = "qa-engineer",
    timestamp: str = "2026-01-01T00:02:00Z",
) -> None:
    """Register a step session under the orchestrator — function 0's correlation."""
    log_store.create_session_log(
        build_registration_entry(
            session_id,
            agent,
            parent_session_id=ORCHESTRATOR_SESSION,
            timestamp=timestamp,
        )
    )


def _deliver(
    log_store: SessionLogStore,
    workspace: Path,
    ref: str = DELIVERED_REF,
    status: str = "approved",
) -> None:
    """Act the in-flight step: its session commits the step's artifact (function 9)."""
    _open_step_session(log_store)
    write_artifact(
        workspace, ref, {"slug": "delivered", "status": status}, session_id=STEP_SESSION
    )


def _build_checker(
    artifact_store: ArtifactStore,
    log_store: SessionLogStore,
    catalog: WorkflowCatalog,
) -> StepPostconditionChecker:
    """Wire the checker with its three constructor-injected collaborators."""
    return StepPostconditionChecker(
        ConditionEvaluator(artifact_store), log_store, catalog
    )


class TestStepPostconditionChecker:
    def test_answers_not_applicable_when_no_step_is_in_flight(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, precondition (E): an in-flight step exists — violation:
        `not-applicable` (rule 2)."""
        _open_session(log_store, with_step=False)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "not-applicable"
        assert len(read_entries(workspace, ORCHESTRATOR_SESSION)) == 1

    def test_absorbs_a_duplicate_step_ended_delivery_as_not_applicable(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, precondition (E) and invariant 2: after the first outcome
        journals, the step is no longer in flight, so re-delivery finds no target —
        enforcing 'evaluated ONCE per step pass' structurally."""
        _open_session(log_store)
        _deliver(log_store, workspace)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        first = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)
        second = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert (first.outcome.status, second.outcome.status) == ("pass", "not-applicable")
        journaled = [
            entry["report"]["context"]["function"]
            for entry in read_entries(workspace, ORCHESTRATOR_SESSION)
        ]
        assert journaled.count("check-step-postconditions") == 1

    def test_reports_state_error_when_the_in_flight_resolution_names_no_instance(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Spec Outcomes rule 1: `step-correlation-missing` is `state-error` — "a step
        correlation is missing or of the wrong kind" — an assignment "fixed by kind,
        never per-function taste"; journaled."""
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
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "state-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "step-correlation-missing"
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)
        assert journaled[-1]["report"]["outcome"]["status"] == "state-error"

    def test_reports_one_check_per_declared_postcondition_with_an_aggregate_pass(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, worked example: `outcome: pass` with one `conditionChecks`
        entry for the declared `report-exists` postcondition — after the built-in
        delivery check (invariant 5)."""
        _open_session(log_store)
        _deliver(log_store, workspace)
        catalog = build_catalog(
            conditions=(
                StateCondition(
                    kind="postcondition",
                    slug="report-exists",
                    set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
                    set_predicate="selected.size() == 1",
                ),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "pass"
        assert [
            (check.condition.slug, check.outcome) for check in report.condition_checks
        ] == [(ARTIFACT_DELIVERED_SLUG, "pass"), ("report-exists", "pass")]

    def test_ignores_declared_preconditions(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
    ) -> None:
        """Function 10, Summary: the same condition machinery as function 5, applied
        to the step's declared POSTCONDITIONS."""
        _open_session(log_store)
        catalog = build_catalog(
            conditions=(
                StepCondition(kind="precondition", slug="after-build", step="build"),
                StepCondition(kind="postcondition", slug="unblocks-ship", step="ship"),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert [check.condition.slug for check in report.condition_checks] == [
            ARTIFACT_DELIVERED_SLUG,
            "unblocks-ship",
        ]

    def test_evaluates_over_the_final_state_the_ended_step_left(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariant 2 and Trigger: postconditions are evaluated at the
        step-ended boundary — the step's session has ended, the state it left is
        FINAL; invariant 1: only persisted artifacts, never agent memory (C2)."""
        _open_session(log_store)
        write_artifact(
            workspace,
            "review-report/r1.json",
            {"slug": "review-report", "status": "approved"},
            commit=False,
        )
        catalog = build_catalog(
            conditions=(
                StateCondition(
                    kind="postcondition",
                    slug="report-exists",
                    set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
                    set_predicate="selected.size() == 1",
                ),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "fail"

    def test_journals_the_step_outcome_function_3s_cursor_reads(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariant 3: the step's outcome logs from this function —
        this journaled outcome is exactly what function 3's cursor reads; a step
        whose latest outcome passes counts as executed."""
        _open_session(log_store)
        _deliver(log_store, workspace)
        catalog = build_catalog(
            conditions=(
                StepCondition(kind="postcondition", slug="unblocks-ship", step="ship"),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["context"]["function"] == "check-step-postconditions"
        assert journaled["context"]["sessionId"] == ORCHESTRATOR_SESSION
        assert journaled["context"]["workflowInstanceId"] == INSTANCE_ID
        assert journaled["outcome"]["status"] == "pass"

    def test_never_passes_vacuously_when_zero_postconditions_are_declared(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariants 4-5: zero DECLARED postconditions still carry the
        built-in delivery check — a step whose session committed nothing fails,
        journaled, and is never counted executed."""
        _open_session(log_store)
        _open_step_session(log_store)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "fail"
        [check] = report.condition_checks
        assert (check.condition.slug, check.outcome) == (ARTIFACT_DELIVERED_SLUG, "fail")
        assert "delivered no 'review-report' artifact" in check.failure_message
        assert f"'{STEP_SESSION}'" in check.failure_message
        assert read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]["outcome"] == {
            "status": "fail"
        }

    def test_fails_when_only_another_sessions_older_instance_exists(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariant 5: an instance of the step's artifact kind committed
        by ANOTHER session — or seeded with no attribution — is not this step's
        delivery; the kind-wide `artifacts[artifact].size() > 0` would wrongly pass."""
        _open_session(log_store)
        write_artifact(
            workspace, "review-report/seeded.json", {"slug": "seeded", "status": "draft"}
        )
        write_artifact(
            workspace,
            "review-report/older.json",
            {"slug": "older", "status": "draft"},
            session_id="01jolderstep",
        )
        _open_step_session(log_store)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "fail"

    def test_passes_when_the_steps_session_committed_its_artifact(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariant 5: the step's session committed an instance of the
        declared artifact kind — the built-in check passes and is journaled under its
        reserved slug with the full harness-defined condition."""
        _open_session(log_store)
        write_artifact(
            workspace, "review-report/seeded.json", {"slug": "seeded", "status": "draft"}
        )
        _deliver(log_store, workspace)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "pass"
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["conditionChecks"] == [
            {
                "condition": {
                    "kind": "postcondition",
                    "slug": ARTIFACT_DELIVERED_SLUG,
                    "setSelector": {
                        "setQuery": (
                            "artifacts[artifact].filter(a, "
                            "a.provenance.sessionId in stepSessionIds)"
                        )
                    },
                    "setPredicate": "selected.size() > 0",
                },
                "outcome": "pass",
            }
        ]

    def test_an_update_of_an_existing_instance_delivers_it(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Artifact provenance: an update-type step satisfies the check by committing
        its edit — provenance names the session of the latest commit on the path."""
        _open_session(log_store)
        write_artifact(
            workspace, DELIVERED_REF, {"slug": "delivered", "status": "draft"}
        )
        _deliver(log_store, workspace, status="approved")
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "pass"

    def test_several_instances_committed_by_the_step_deliver_it(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariant 5: the check is "at least one instance of the declared
        kind committed by this step's session" — a step committing several instances in
        one pass delivers, beside instances other sessions own."""
        _open_session(log_store)
        write_artifact(
            workspace,
            "review-report/older.json",
            {"slug": "older", "status": "draft"},
            session_id="01jolderstep",
        )
        _open_step_session(log_store)
        for slug in ("first", "second", "third"):
            write_artifact(
                workspace,
                f"review-report/{slug}.json",
                {"slug": slug, "status": "draft"},
                session_id=STEP_SESSION,
            )
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "pass"

    def test_ignores_a_session_registered_before_the_in_flight_resolution(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariant 5: the step's sessions are those registered under the
        orchestrator for the step's actor at or after the in-flight resolution — an
        earlier pass's session is not this pass's step session."""
        _open_session(log_store)
        _open_step_session(log_store, "01jearlier", timestamp="2026-01-01T00:00:30Z")
        write_artifact(
            workspace,
            DELIVERED_REF,
            {"slug": "delivered", "status": "approved"},
            session_id="01jearlier",
        )
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "fail"

    def test_never_touches_an_artifact(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, Postconditions: no artifact is touched — the invocation's own
        log entry is the only write."""
        _open_session(log_store)
        write_artifact(
            workspace,
            "review-report/r1.json",
            {"slug": "review-report", "status": "draft"},
        )
        head_before = run_git(workspace, "rev-parse", "HEAD")
        checker = _build_checker(artifact_store, log_store, build_catalog())

        checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert run_git(workspace, "rev-parse", "HEAD") == head_before
        assert run_git(workspace, "status", "--porcelain", "--", "review-report") == ""

    def test_refuses_an_ended_session_without_journaling(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """C8 / rule 3: the refusal is `state-error` with code `session-ended`, never
        journaled — no entry may follow the ending entry."""
        _open_session(log_store)
        log_store.append_log_entry(
            ORCHESTRATOR_SESSION, build_ending_entry(ORCHESTRATOR_SESSION)
        )
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert (report.outcome.status, report.outcome.error.code) == (
            "state-error",
            "session-ended",
        )
        assert read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]["context"][
            "function"
        ] == "end-session"

    def test_reports_an_unregistered_session_as_an_inquiry_error_without_a_log(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Rule 1: `session-unregistered` is an `inquiry-error`; rule 4: it returns
        its report but has no log to journal to."""
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions("01jmissing", None)

        assert (report.outcome.status, report.outcome.error.code) == (
            "inquiry-error",
            "session-unregistered",
        )
        assert read_entries(workspace, "01jmissing") == []

    def test_reports_a_runtime_condition_failure_as_a_journaled_state_error(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariant 1: a CEL expression failing at runtime is
        `state-error` (`condition-evaluation-failed`), journaled, exactly as
        function 5, invariant 2."""
        _open_session(log_store)
        catalog = build_catalog(
            conditions=(
                StateCondition(
                    kind="postcondition",
                    slug="report-exists",
                    set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
                    set_predicate="selected[7].slug == 'x'",
                ),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.error.code == "condition-evaluation-failed"
        assert read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]["outcome"][
            "error"
        ]["code"] == "condition-evaluation-failed"

    def test_reports_an_unreadable_environment_as_a_system_error(
        self,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Rule 1: the environment failing is the uniform `system-error`."""
        checker = _build_checker(
            artifact_store, _FailingSessionLogStore(workspace), build_catalog()
        )

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "system-error"

    def test_returns_a_report_bound_to_its_own_output_contract(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
    ) -> None:
        """Classes: structurally identical payloads bound to DISTINCT output
        contracts — which function produced a report is read from `context.function`."""
        _open_session(log_store)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert isinstance(report, CheckStepPostconditionsReport)
        assert report.context.function == "check-step-postconditions"
