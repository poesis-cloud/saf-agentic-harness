"""Unit tests for `StepAuthorizationChecker` — function 8, `check-step-authorization`."""

from __future__ import annotations

from pathlib import Path

from checking_fixtures import (
    build_ending_entry,
    build_registration_entry,
    build_step,
    build_step_resolution_entry,
    read_entries,
    run_git,
    write_artifact,
)
from config import AccessControlList, WorkspaceLayout
from errors import SystemFailureError
from services.checking import AuthorizationReport, StepAuthorizationChecker
from stores.artifact_store import ArtifactStore
from stores.session_log_store import SessionLogStore
from write_boundary_fixtures import (
    ABSENT_LOGS_REF,
    ACTOR,
    ARTIFACT_KIND,
    ARTIFACT_REF,
    EXAMPLE_REF,
    EXAMPLE_RESOURCE,
    OUTSIDE_REF,
    build_access_control_list,
    build_ambiguous_layout,
    build_example_layout,
    build_logs_binding_layout,
    build_report_document,
    list_contract_violations,
    stage_artifact,
)

SESSION = "01j9xqr7t3"
VALID_DOCUMENT = build_report_document()


class _FailingSessionLogStore(SessionLogStore):
    """A fake store whose reads fail the way an unreadable environment does."""

    def load_session_log(self, session_id: str):
        """Fail the read exactly as an unreadable log does (rule 1, `system-error`)."""
        raise SystemFailureError("log-unreadable", "Session log is unreadable.", True)


def _open_session(
    log_store: SessionLogStore,
    agent: str = ACTOR,
    *,
    with_step: bool = False,
) -> None:
    """Register the acting session — the registration function 8 reads its actor from."""
    log_store.create_session_log(build_registration_entry(SESSION, agent))
    if with_step:
        log_store.append_log_entry(
            SESSION, build_step_resolution_entry(SESSION, build_step())
        )


def _build_checker(
    log_store: SessionLogStore,
    access_control_list: AccessControlList,
    workspace_layout: WorkspaceLayout,
    artifact_store: ArtifactStore,
) -> StepAuthorizationChecker:
    """Wire the checker with its four constructor-injected collaborators."""
    return StepAuthorizationChecker(
        log_store, access_control_list, workspace_layout, artifact_store
    )


class TestStepAuthorizationChecker:
    def test_allows_a_granted_write_onto_a_clean_baseline(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, Interface: allow — the `authorization` object carries the
        actor, the path, the action, and the resolved resource, and an allow carries
        no failure message (output contract, `allowed` branch)."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )

        assert report.outcome.status == "allowed"
        assert report.authorization.actor == ACTOR
        assert report.authorization.artifact_path == ARTIFACT_REF
        assert report.authorization.action == "create"
        assert report.authorization.resource == ARTIFACT_KIND
        assert report.authorization.failure_message is None
        assert list_contract_violations(report) == ()

    def test_derives_the_actor_from_the_registered_session_never_the_inquiry(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 1: the actor is the AGENT derived from the
        REGISTERED host session, never a function input — a session registered to a
        privilege-less agent is denied even though the ACL grants the same write to
        another agent."""
        _open_session(log_store, "product-owner")
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )

        assert report.outcome.status == "denied"
        assert report.authorization.actor == "product-owner"

    def test_resolves_the_resource_from_the_write_path(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 2: the resource is the artifact's SCHEMA identity,
        resolved from the write path via the workspace layout — never named by the
        caller."""
        _open_session(log_store, with_step=True)
        checker = _build_checker(
            log_store,
            build_access_control_list(privileges=((EXAMPLE_RESOURCE, "update"),)),
            build_example_layout(),
            artifact_store,
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(EXAMPLE_REF),
            action="update",
        )

        assert report.outcome.status == "allowed"
        assert report.authorization.resource == EXAMPLE_RESOURCE

    def test_ignores_a_property_fragment_on_the_artifact_path(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 8, invariant 3: authorization is whole-resource — any `#property`
        suffix on an artifact path is ignored, so the fragmented path decides exactly
        as the bare path does, the staging baseline (invariant 5) included."""
        _open_session(log_store)
        write_artifact(workspace, ARTIFACT_REF, dict(VALID_DOCUMENT))
        stage_artifact(workspace, ARTIFACT_REF, build_report_document(status="approved"))
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(f"{ARTIFACT_REF}#status"),
            action="update",
        )

        assert report.outcome.status == "denied"
        assert report.authorization.resource == ARTIFACT_KIND
        assert "staging baseline" in report.authorization.failure_message

    def test_allows_a_fragmented_path_whose_bare_path_is_authorized(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 3: the fragment never changes the resource under
        test — path-level and property-level granularity are intentionally not
        modelled."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(f"{ARTIFACT_REF}#status"),
            action="create",
        )

        assert report.outcome.status == "allowed"
        assert report.authorization.resource == ARTIFACT_KIND

    def test_denies_a_write_no_role_grants_naming_the_missing_privilege(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, worked example: the `qa-engineer` updating
        `portfolio/epics/epic-payments.md` is `denied`, the `authorization`
        `failureMessage` naming the missing privilege — invariant 4: no implicit
        grants, and denial IS the enforcement."""
        _open_session(log_store, with_step=True)
        checker = _build_checker(
            log_store,
            build_access_control_list(privileges=((EXAMPLE_RESOURCE, "create"),)),
            build_example_layout(),
            artifact_store,
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id="01j9xq0f2m",
            artifact_path=Path(EXAMPLE_REF),
            action="update",
        )

        assert report.outcome.status == "denied"
        assert report.authorization.resource == EXAMPLE_RESOURCE
        assert "update" in report.authorization.failure_message
        assert EXAMPLE_RESOURCE in report.authorization.failure_message
        assert list_contract_violations(report) == ()

    def test_denies_every_delete_even_when_a_role_grants_it(
        self,
        log_store: SessionLogStore,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, ACL design principles: `delete` is a FORWARD DECLARATION — the
        verb exists and roles may grant it, but this function denies every `delete`
        unconditionally until it is modeled (invariant 4 names the cause)."""
        _open_session(log_store)
        checker = _build_checker(
            log_store,
            build_access_control_list(privileges=((ARTIFACT_KIND, "delete"),)),
            workspace_layout,
            artifact_store,
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="delete",
        )

        assert report.outcome.status == "denied"
        assert "delete" in report.authorization.failure_message

    def test_denies_a_path_outside_the_artifact_layout(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 4: an unresolvable resource is denied, its cause
        named — invariant 5 puts paths OUTSIDE the artifact layout on the same
        boundary."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(OUTSIDE_REF),
            action="create",
        )

        assert report.outcome.status == "denied"
        assert OUTSIDE_REF in report.authorization.failure_message
        assert list_contract_violations(report) == ()

    def test_names_no_resource_on_a_deny_whose_cause_is_that_none_resolves(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 2: "the resource is the artifact's schema identity,
        resolved from the write path" — when nothing resolves, invariant 4 admits the
        deny for "the unresolvable resource", so the decision carries NO resource. A
        sentinel would be a syntactically valid `artifactSlug`, indistinguishable from a
        real answer."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(OUTSIDE_REF),
            action="create",
        )

        assert report.authorization.resource is None
        assert "resource" not in report.to_dict()["authorization"]
        assert list_contract_violations(report) == ()

    def test_names_no_resource_on_the_logs_path_deny(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 6: a write to the logs path is denied for every actor
        because "the ACL vocabulary has no resource for it" — so the decision names
        none."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ABSENT_LOGS_REF),
            action="create",
        )

        assert report.outcome.status == "denied"
        assert report.authorization.resource is None
        assert "resource" not in report.to_dict()["authorization"]
        assert list_contract_violations(report) == ()

    def test_an_allow_always_names_the_resource_it_was_granted_over(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 2: an ALLOW is a decision about a resolved resource, so
        the `allowed` branch still requires it — moving `resource` off the shared
        definition must not weaken the branch that has an answer."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )

        assert report.outcome.status == "allowed"
        assert report.authorization.resource == ARTIFACT_KIND
        assert list_contract_violations(report) == ()

    def test_denies_a_path_several_artifact_kinds_claim(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 2 + 4: the resource must resolve to ONE artifact
        schema identity; a path several kinds' patterns match resolves to none of
        them here — function 8 has no artifact `type` to disambiguate with — so it is
        denied as an unresolvable resource rather than authorized by guesswork."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, build_ambiguous_layout(), artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )

        assert report.outcome.status == "denied"
        assert list_contract_violations(report) == ()

    def test_denies_a_logs_path_for_every_actor(
        self,
        log_store: SessionLogStore,
        artifact_store: ArtifactStore,
    ) -> None:
        """Function 8, invariant 6: a write targeting the workspace logs path is
        denied ALWAYS, for every actor — logs are harness-authored, single-writer
        (C0), so no agent privilege can grant authorship of the journal.

        The layout here BINDS the logs path to an artifact kind the actor is fully
        privileged on, and the path is one no session has opened — so its staging
        baseline is clean too. Every other deny cause is out of the way: only
        invariant 6 can refuse this write.
        """
        _open_session(log_store)
        checker = _build_checker(
            log_store,
            build_access_control_list(privileges=((ARTIFACT_KIND, "create"),)),
            build_logs_binding_layout(),
            artifact_store,
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ABSENT_LOGS_REF),
            action="create",
        )

        assert report.outcome.status == "denied"
        assert ABSENT_LOGS_REF in report.authorization.failure_message
        assert list_contract_violations(report) == ()

    def test_denies_a_dirty_tracked_target(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 8, invariant 5: a write whose staging baseline is not clean
        against `HEAD` is denied at the same boundary — a DIRTY TRACKED target does
        not execute, so the staged write is always the only staged content at its
        path (C6)."""
        _open_session(log_store)
        write_artifact(workspace, ARTIFACT_REF, dict(VALID_DOCUMENT))
        stage_artifact(workspace, ARTIFACT_REF, build_report_document(status="approved"))
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="update",
        )

        assert report.outcome.status == "denied"
        assert report.authorization.failure_message

    def test_denies_a_pre_existing_untracked_target(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 8, invariant 5: a PRE-EXISTING UNTRACKED target is unclean too —
        the baseline admits only an absent path or a tracked-and-clean one."""
        _open_session(log_store)
        stage_artifact(workspace, ARTIFACT_REF, dict(VALID_DOCUMENT))
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )

        assert report.outcome.status == "denied"
        assert report.authorization.failure_message

    def test_allows_a_write_over_a_tracked_and_clean_target(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 8, invariant 5: the baseline admits a TRACKED-AND-CLEAN target —
        the deny is the unclean baseline, never the mere existence of the path."""
        _open_session(log_store)
        write_artifact(workspace, ARTIFACT_REF, dict(VALID_DOCUMENT))
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="update",
        )

        assert report.outcome.status == "allowed"

    def test_journals_one_entry_per_authorization_decision(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 8, Postconditions: ONE log entry per authorization decision —
        allow and deny alike; invariant 4: every deny is an ordinary `denied`
        outcome, journaled."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )
        checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="delete",
        )

        entries = read_entries(workspace, SESSION)
        decisions = [
            entry["report"]
            for entry in entries
            if entry["report"]["context"]["function"] == "check-step-authorization"
        ]
        assert [decision["outcome"]["status"] for decision in decisions] == [
            "allowed",
            "denied",
        ]
        assert decisions[1]["authorization"]["failureMessage"]

    def test_never_writes_to_the_workspace_on_a_deny(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Function 8, Postconditions: on a deny the write never lands — the workspace
        never sees unauthorized bytes, so the decision itself touches neither the
        working tree nor `HEAD`."""
        _open_session(log_store)
        head_before = run_git(workspace, "rev-parse", "HEAD")
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="delete",
        )

        assert run_git(workspace, "rev-parse", "HEAD") == head_before
        assert not (workspace / ARTIFACT_REF).exists()
        assert run_git(workspace, "status", "--porcelain", "--", ARTIFACT_KIND) == ""

    def test_refuses_an_ended_session_without_journaling(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """C8 / rule 3: a session-bound call against a session whose log carries an
        ending entry returns `state-error` with code `session-ended` — and is NOT
        journaled: no entry ever follows the ending entry."""
        _open_session(log_store)
        log_store.append_log_entry(SESSION, build_ending_entry(SESSION))
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
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
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Rule 1: `session-unregistered` is an `inquiry-error` — the mediated
        backstop; rule 4: it returns its report but has no log to journal to."""
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id="01jmissing",
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error.code == "session-unregistered"
        assert read_entries(workspace, "01jmissing") == []

    def test_reports_an_unreadable_environment_as_a_system_error(
        self,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        workspace: Path,
    ) -> None:
        """Rule 1: the environment failing — here an unreadable log — is the uniform
        `system-error`, retryable, carrying no function payload."""
        checker = _build_checker(
            _FailingSessionLogStore(workspace),
            access_control_list,
            workspace_layout,
            artifact_store,
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )

        assert report.outcome.status == "system-error"
        assert report.outcome.error.retryable is True
        assert report.authorization is None
        assert list_contract_violations(report) == ()

    def test_returns_a_report_bound_to_its_own_output_contract(
        self,
        log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
    ) -> None:
        """Classes: every service returns a concrete `Report` subtype, and which
        function produced it is read from `context.function`."""
        _open_session(log_store)
        checker = _build_checker(
            log_store, access_control_list, workspace_layout, artifact_store
        )

        report = checker.check_step_authorization(
            session_id=SESSION,
            parent_session_id=None,
            artifact_path=Path(ARTIFACT_REF),
            action="create",
        )

        assert isinstance(report, AuthorizationReport)
        assert report.context.function == "check-step-authorization"
