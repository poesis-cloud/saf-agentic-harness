"""Unit tests for `StepPreconditionChecker` — function 5, `check-step-preconditions`."""

from __future__ import annotations

from pathlib import Path

from checking_fixtures import (
    INSTANCE_ID,
    ORCHESTRATOR_SESSION,
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
from config import StateCondition, StepCondition, WorkflowCatalog
from errors import SystemFailureError
from services.checking import (
    CheckStepPreconditionsReport,
    ConditionEvaluator,
    StepPreconditionChecker,
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
) -> StepPreconditionChecker:
    """Wire the checker with its three constructor-injected collaborators."""
    return StepPreconditionChecker(
        ConditionEvaluator(artifact_store), log_store, catalog
    )


class TestStepPreconditionChecker:
    def test_answers_not_applicable_when_no_step_is_in_flight(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 5, precondition (E): a resolved step is in hand — an in-flight
        step in the invoking session — violation: `not-applicable` (rule 2)."""
        _open_session(log_store, with_step=False)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "not-applicable"
        assert len(read_entries(workspace, ORCHESTRATOR_SESSION)) == 1

    def test_never_journals_a_not_applicable_outcome(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Rule 2: `not-applicable` is a success status, NEVER journaled — the one
        explicit exception to 1 invocation = 1 entry."""
        _open_session(log_store, with_step=False)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        functions = [
            entry["report"]["context"]["function"]
            for entry in read_entries(workspace, ORCHESTRATOR_SESSION)
        ]
        assert "check-step-preconditions" not in functions

    def test_reports_one_check_per_declared_precondition_with_an_aggregate_pass(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 5, Interface: the aggregate `outcome` (pass / fail) plus
        `conditionChecks` — one check per declared precondition."""
        _open_session(log_store)
        write_artifact(
            workspace,
            "review-report/r1.json",
            {"slug": "review-report", "status": "draft"},
        )
        catalog = build_catalog(
            conditions=(
                StateCondition(
                    kind="precondition",
                    slug="report-exists",
                    set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
                    set_predicate="selected.size() == 1",
                ),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "pass"
        assert [check.condition.slug for check in report.condition_checks] == [
            "report-exists"
        ]

    def test_aggregates_to_fail_when_any_declared_precondition_fails(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
    ) -> None:
        """Function 5, worked example: a report whose aggregate outcome is `fail`
        carries a passing stepCondition beside a failing stateCondition."""
        _open_session(log_store)
        catalog = build_catalog(
            conditions=(
                StepCondition(kind="precondition", slug="after-build", step="build"),
                StateCondition(
                    kind="precondition",
                    slug="report-exists",
                    set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
                    set_predicate="selected.size() == 1",
                ),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "fail"
        assert [check.outcome for check in report.condition_checks] == ["fail", "fail"]
        assert all(check.failure_message for check in report.condition_checks)

    def test_ignores_declared_postconditions(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
    ) -> None:
        """Function 5, Interface: one check per declared PRECONDITION — a step's
        postconditions belong to function 10, over the same flat conditions list."""
        _open_session(log_store)
        catalog = build_catalog(
            conditions=(
                StepCondition(kind="precondition", slug="after-build", step="build"),
                StepCondition(kind="postcondition", slug="unblocks-ship", step="ship"),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert [check.condition.slug for check in report.condition_checks] == [
            "after-build"
        ]

    def test_passes_vacuously_and_journals_when_zero_preconditions_are_declared(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 5, invariant 4: a step declaring zero preconditions passes
        vacuously — `outcome: pass` with an empty `conditionChecks` array, an
        explicit, JOURNALED entry, never a skipped invocation."""
        _open_session(log_store)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert (report.outcome.status, report.condition_checks) == ("pass", ())
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]
        assert journaled["report"]["context"]["function"] == "check-step-preconditions"
        assert journaled["report"]["conditionChecks"] == []

    def test_journals_the_invocation_to_the_dispatching_session_log(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 5, Postconditions: one log entry records the invocation — the
        per-condition checks plus the aggregate outcome — appended to the
        DISPATCHING (orchestrator) session's log."""
        _open_session(log_store)
        catalog = build_catalog(
            conditions=(
                StepCondition(kind="precondition", slug="after-build", step="build"),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["context"]["sessionId"] == ORCHESTRATOR_SESSION
        assert journaled["context"]["workflowInstanceId"] == INSTANCE_ID
        assert journaled["outcome"]["status"] == "fail"
        assert journaled["conditionChecks"][0]["condition"]["slug"] == "after-build"

    def test_never_touches_an_artifact(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 5, Postconditions: no artifact is touched — the invocation's own
        log entry is the only write: checking never mutates artifacts."""
        _open_session(log_store)
        write_artifact(
            workspace,
            "review-report/r1.json",
            {"slug": "review-report", "status": "draft"},
        )
        head_before = run_git(workspace, "rev-parse", "HEAD")
        checker = _build_checker(artifact_store, log_store, build_catalog())

        checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert run_git(workspace, "rev-parse", "HEAD") == head_before
        assert run_git(workspace, "status", "--porcelain", "--", "review-report") == ""

    def test_refuses_an_ended_session_without_journaling(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """C8 / rule 3: a session-bound call against a session whose log carries an
        ending entry returns `state-error` with code `session-ended` — and is NOT
        journaled: no entry ever follows the ending entry."""
        _open_session(log_store)
        log_store.append_log_entry(
            ORCHESTRATOR_SESSION, build_ending_entry(ORCHESTRATOR_SESSION)
        )
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-ended"
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

        report = checker.check_step_preconditions("01jmissing", None)

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error.code == "session-unregistered"
        assert read_entries(workspace, "01jmissing") == []

    def test_reports_a_runtime_condition_failure_as_a_journaled_state_error(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
        workspace: Path,
    ) -> None:
        """Function 5, invariant 2: a CEL expression failing AT RUNTIME is
        `state-error` (`condition-evaluation-failed`), journaled, the error detail
        naming the condition slug."""
        _open_session(log_store)
        catalog = build_catalog(
            conditions=(
                StateCondition(
                    kind="precondition",
                    slug="report-exists",
                    set_selector={"setQuery": SELECT_DECLARED_ARTIFACT},
                    set_predicate="selected[7].slug == 'x'",
                ),
            )
        )
        checker = _build_checker(artifact_store, log_store, catalog)

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "condition-evaluation-failed"
        assert "report-exists" in report.outcome.error.message
        journaled = read_entries(workspace, ORCHESTRATOR_SESSION)[-1]["report"]
        assert journaled["outcome"]["error"]["code"] == "condition-evaluation-failed"

    def test_reports_an_unreadable_environment_as_a_system_error(
        self,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 5, precondition (C) and rule 1: an unreadable environment is the
        uniform `system-error`."""
        checker = _build_checker(
            artifact_store, _FailingSessionLogStore(workspace), build_catalog()
        )

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "system-error"
        assert report.outcome.error.retryable is True

    def test_returns_a_report_bound_to_its_own_output_contract(
        self,
        artifact_store: ArtifactStore,
        log_store: SessionLogStore,
    ) -> None:
        """Classes: functions 5 and 10 return structurally identical payloads bound
        to DISTINCT output contracts, so each is its own leaf type; which function
        produced a report is read from `context.function`."""
        _open_session(log_store)
        checker = _build_checker(artifact_store, log_store, build_catalog())

        report = checker.check_step_preconditions(ORCHESTRATOR_SESSION, None)

        assert isinstance(report, CheckStepPreconditionsReport)
        assert report.context.function == "check-step-preconditions"
