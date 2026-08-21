"""Unit tests for `StepArtifactChecker` — function 9, `check-step-artifact`."""

from __future__ import annotations

from pathlib import Path

from checking_fixtures import (
    build_ending_entry,
    build_registration_entry,
    read_entries,
    run_git,
    write_artifact,
)
from errors import SystemFailureError
from services.checking import ArtifactCheckReport, StepArtifactChecker
from stores.artifact_store import ArtifactStore
from stores.session_log_store import SessionLogStore
from write_boundary_fixtures import (
    ACTOR,
    ARTIFACT_REF,
    OUTSIDE_REF,
    SIBLING_REF,
    build_report_document,
    build_workspace_layout,
    list_contract_violations,
    read_document,
    stage_artifact,
)

SESSION = "01j9xqr7t3"
VALID = build_report_document()
SIBLING_VALID = build_report_document(slug="chargebacks")
INVALID = build_report_document(status="shipped")
SIBLING_INVALID = build_report_document(slug="chargebacks", status="shipped")


class _FailingSessionLogStore(SessionLogStore):
    """A fake store whose reads fail the way an unreadable environment does."""

    def load_session_log(self, session_id: str):
        """Fail the read exactly as an unreadable log does (rule 1, `system-error`)."""
        raise SystemFailureError("log-unreadable", "Session log is unreadable.", True)


class _FailingCommitArtifactStore(ArtifactStore):
    """A fake store whose commit fails the way a locked Git index does."""

    def commit_artifacts(self, artifact_paths, *, session_id: str) -> None:
        """Fail mid-commit exactly as the Git plane does (function 9, invariant 5)."""
        raise SystemFailureError("git-failed", "git commit failed: index.lock", True)


def _open_session(log_store: SessionLogStore) -> None:
    """Register the acting session whose log the validation entry belongs to."""
    log_store.create_session_log(build_registration_entry(SESSION, ACTOR))


def _count_commits(workspace: Path) -> int:
    """Count the commits reachable from `HEAD` — the workspace state's advance."""
    return int(run_git(workspace, "rev-list", "--count", "HEAD"))


class TestStepArtifactChecker:
    def test_commits_a_valid_set_as_exactly_one_commit_attributed_to_the_session(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 9, invariant 3: a fully valid set is COMMITTED in the same act —
        1 validated set = 1 commit, attributed to the acting session (its `sessionId`
        in the commit message) so Git history and the session log correlate."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        stage_artifact(workspace, SIBLING_REF, SIBLING_VALID)
        commits_before = _count_commits(workspace)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF), Path(SIBLING_REF)),
        )

        assert report.outcome.status == "valid"
        assert report.artifact_checks == ()
        assert _count_commits(workspace) == commits_before + 1
        assert SESSION in run_git(workspace, "log", "-1", "--format=%B")
        committed = run_git(workspace, "ls-tree", "-r", "--name-only", "HEAD")
        assert ARTIFACT_REF in committed and SIBLING_REF in committed
        assert list_contract_violations(report) == ()

    def test_reverts_the_whole_set_when_any_path_is_invalid(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 9, invariant 2: ANY invalid path reverts the WHOLE set —
        call-level atomicity: every staged path of the call is discarded, and entries
        for valid siblings never appear, their revert implied by set membership."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        stage_artifact(workspace, SIBLING_REF, SIBLING_INVALID)
        commits_before = _count_commits(workspace)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF), Path(SIBLING_REF)),
        )

        assert report.outcome.status == "reverted"
        assert [check.artifact_path for check in report.artifact_checks] == [
            SIBLING_REF
        ]
        assert read_document(workspace, ARTIFACT_REF) is None
        assert read_document(workspace, SIBLING_REF) is None
        assert _count_commits(workspace) == commits_before
        assert list_contract_violations(report) == ()

    def test_names_every_failing_path_in_the_reverted_report(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 9, invariant 2: the failure messages NAME EACH failing path so the
        agent retries — one record per failing path, never one for the set."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, INVALID)
        stage_artifact(workspace, SIBLING_REF, SIBLING_INVALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF), Path(SIBLING_REF)),
        )

        assert sorted(check.artifact_path for check in report.artifact_checks) == [
            SIBLING_REF,
            ARTIFACT_REF,
        ]
        assert all(check.failure_message for check in report.artifact_checks)

    def test_restores_a_tracked_path_from_head_and_deletes_a_newly_created_one(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 9, invariant 2: the discard restores TRACKED paths from `HEAD` and
        DELETES newly created ones — and never touches workspace state, since the
        invalid bytes existed only in staging."""
        _open_session(log_store)
        write_artifact(workspace, ARTIFACT_REF, dict(VALID))
        stage_artifact(workspace, ARTIFACT_REF, INVALID)
        stage_artifact(workspace, SIBLING_REF, SIBLING_INVALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF), Path(SIBLING_REF)),
        )

        reverts = {
            check.artifact_path: (check.revert.action, check.revert.from_ref)
            for check in report.artifact_checks
        }
        assert reverts[ARTIFACT_REF] == ("restored", "HEAD")
        assert reverts[SIBLING_REF] == ("deleted", None)
        assert read_document(workspace, ARTIFACT_REF) == dict(VALID)
        assert read_document(workspace, SIBLING_REF) is None

    def test_never_advances_committed_state_with_invalid_bytes(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 9, Postconditions / C6: committed state never contained the
        invalid bytes — the whole staged set was discarded, so `HEAD` still holds
        exactly what it held before the write."""
        _open_session(log_store)
        write_artifact(workspace, ARTIFACT_REF, dict(VALID))
        head_before = run_git(workspace, "rev-parse", "HEAD")
        stage_artifact(workspace, ARTIFACT_REF, INVALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        assert run_git(workspace, "rev-parse", "HEAD") == head_before
        assert "shipped" not in run_git(workspace, "show", f"HEAD:{ARTIFACT_REF}")

    def test_validates_vacuously_and_creates_no_commit_for_bytes_identical_to_head(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 9, invariant 4: a staged path byte-identical to `HEAD` validates
        vacuously and stages nothing — a set of only such paths reports `valid` and
        creates NO commit: `valid` asserts validity, not that a commit occurred."""
        _open_session(log_store)
        write_artifact(workspace, ARTIFACT_REF, dict(VALID))
        commits_before = _count_commits(workspace)
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        assert report.outcome.status == "valid"
        assert _count_commits(workspace) == commits_before
        assert run_git(workspace, "diff", "--cached", "--name-only") == ""

    def test_discards_the_whole_set_when_a_path_resolves_to_no_artifact_schema(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 9, precondition (E): every written path resolves to an artifact
        schema — violation: `state-error` (`artifact-schema-unresolved`), journaled,
        and the whole staged set is discarded DEFENSIVELY."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        stage_artifact(workspace, OUTSIDE_REF, VALID)
        commits_before = _count_commits(workspace)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF), Path(OUTSIDE_REF)),
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "artifact-schema-unresolved"
        assert report.artifact_checks == ()
        assert read_document(workspace, ARTIFACT_REF) is None
        assert read_document(workspace, OUTSIDE_REF) is None
        assert _count_commits(workspace) == commits_before
        assert list_contract_violations(report) == ()
        journaled = read_entries(workspace, SESSION)[-1]["report"]
        assert journaled["outcome"]["error"]["code"] == "artifact-schema-unresolved"

    def test_journals_one_entry_per_write_validation_carrying_the_revert_records(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 9, Postconditions: ONE log entry per write validation
        (`valid` / `reverted`) covering the whole set — when reverted, the SAME
        entry's report carries the failing paths' revert records, so there is no
        second revert entry."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, INVALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        validations = [
            entry["report"]
            for entry in read_entries(workspace, SESSION)
            if entry["report"]["context"]["function"] == "check-step-artifact"
        ]
        assert len(validations) == 1
        assert validations[0]["outcome"]["status"] == "reverted"
        assert validations[0]["artifactChecks"][0]["revert"] == {"action": "deleted"}

    def test_omits_the_checks_property_from_a_valid_report(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Classes: an empty `artifact_checks` tuple renders as the property's
        ABSENCE in the `valid` contract branch."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        assert "artifactChecks" not in report.to_dict()

    def test_reports_a_git_failure_mid_commit_as_a_system_error(
        self,
        log_store: SessionLogStore,
        artifact_schema: Path,
        workspace: Path,
    ) -> None:
        """Function 9, invariant 5: a Git failure mid-commit or mid-discard is the
        uniform `system-error` (rule 1) — the staged state is whatever the failure
        left, C6's detect-and-remediate plane owning residual cleanup."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        checker = StepArtifactChecker(
            log_store,
            _FailingCommitArtifactStore(
                workspace, {"review-report": artifact_schema}, build_workspace_layout()
            ),
        )

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        assert report.outcome.status == "system-error"
        assert report.outcome.error.retryable is True
        assert report.artifact_checks == ()
        assert list_contract_violations(report) == ()

    def test_refuses_an_ended_session_without_journaling(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """C8 / rule 3: a session-bound call against a session whose log carries an
        ending entry returns `state-error` with code `session-ended` — and is NOT
        journaled: no entry ever follows the ending entry."""
        _open_session(log_store)
        log_store.append_log_entry(SESSION, build_ending_entry(SESSION))
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-ended"
        assert (
            read_entries(workspace, SESSION)[-1]["report"]["context"]["function"]
            == "end-session"
        )

    def test_reports_an_unregistered_session_as_an_inquiry_error_without_a_log(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Rule 1: `session-unregistered` is an `inquiry-error` — the mediated
        backstop; rule 4: it returns its report but has no log to journal to."""
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id="01jmissing",
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error.code == "session-unregistered"
        assert read_entries(workspace, "01jmissing") == []

    def test_reports_an_unreadable_environment_as_a_system_error(
        self,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Rule 1: the environment failing — here an unreadable log — is the uniform
        `system-error`, retryable, carrying no function payload."""
        checker = StepArtifactChecker(_FailingSessionLogStore(workspace), artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        assert report.outcome.status == "system-error"
        assert report.outcome.error.retryable is True

    def test_returns_a_report_bound_to_its_own_output_contract(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Classes: every service returns a concrete `Report` subtype, and which
        function produced it is read from `context.function`."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, VALID)
        checker = StepArtifactChecker(log_store, artifact_store)

        report = checker.check_step_artifact(
            session_id=SESSION,
            parent_session_id=None,
            artifact_paths=(Path(ARTIFACT_REF),),
        )

        assert isinstance(report, ArtifactCheckReport)
        assert report.context.function == "check-step-artifact"
