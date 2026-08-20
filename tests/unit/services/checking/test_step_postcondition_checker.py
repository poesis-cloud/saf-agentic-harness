"""Unit tests for `StepPostconditionChecker` — function 10, `check-step-postconditions`."""

from __future__ import annotations

from pathlib import Path

from checking_fixtures import (
    INSTANCE_ID,
    ORCHESTRATOR_SESSION,
    build_catalog,
    build_ending_entry,
    build_registration_entry,
    build_step,
    build_step_resolution_entry,
    read_entries,
    run_git,
    write_artifact,
)
from config import StateCondition, StepCondition, WorkflowCatalog
from errors import SystemFailureError
from services.checking import (
    CheckStepPostconditionsReport,
    ConditionEvaluator,
    StepPostconditionChecker,
)
from stores.artifact_store import ArtifactStore
from stores.session_log_store import SessionLogStore

SELECT_DECLARED_ARTIFACT = "artifacts['review-report'].filter(a, a.slug == artifact)"


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
        checker = _build_checker(artifact_store, log_store, build_catalog())

        first = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)
        second = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert (first.outcome.status, second.outcome.status) == ("pass", "not-applicable")
        journaled = [
            entry["report"]["context"]["function"]
            for entry in read_entries(workspace, ORCHESTRATOR_SESSION)
        ]
        assert journaled.count("check-step-postconditions") == 1

    def test_reports_one_check_per_declared_postcondition_with_an_aggregate_pass(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, worked example: `outcome: pass` with one `conditionChecks`
        entry for the declared `report-exists` postcondition."""
        _open_session(log_store)
        write_artifact(
            workspace,
            "review-report/r1.json",
            {"slug": "review-report", "status": "approved"},
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

        assert report.outcome.status == "pass"
        assert [
            (check.condition.slug, check.outcome) for check in report.condition_checks
        ] == [("report-exists", "pass")]

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
            "unblocks-ship"
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

    def test_passes_vacuously_and_journals_when_zero_postconditions_are_declared(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 10, invariant 4: a step declaring zero postconditions passes
        vacuously — `outcome: pass` with an empty `conditionChecks` array, JOURNALED,
        and sufficient for the cursor to count the step executed."""
        _open_session(log_store)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_postconditions(ORCHESTRATOR_SESSION, None)

        assert (report.outcome.status, report.condition_checks) == ("pass", ())
        assert read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"][
            "conditionChecks"
        ] == []

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
