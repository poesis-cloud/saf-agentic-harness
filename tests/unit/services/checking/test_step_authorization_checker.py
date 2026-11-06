"""Unit tests for `StepAuthorizationChecker` — function 8, `check-step-authorization`."""

from __future__ import annotations

from pathlib import Path

from checking_fixtures import (
    INSTANCE_ID,
    ORCHESTRATOR_SESSION,
    build_ending_entry,
    build_entry,
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
    with_step: bool = True,
    session_id: str = SESSION,
    step_slug: str = "review",
    orchestrator: str = ORCHESTRATOR_SESSION,
    registered_at: str = "2026-01-01T00:01:30Z",
) -> str | None:
    """Register the acting session — the registration function 8 reads its actor from.

    By default it is the WORKER session of an in-flight step instance: its
    orchestrator session resolved the step, and its step ref exists (function 8,
    invariant 7). Answers that step ref's name. `with_step=False` opens a root
    session instead — no step in flight.
    """
    if not with_step:
        log_store.create_session_log(build_registration_entry(session_id, agent))
        return None
    workspace = log_store._workspace_dir
    if not (workspace / "logs" / f"{orchestrator}.log.jsonl").exists():
        log_store.create_session_log(
            build_registration_entry(orchestrator, "orchestrator")
        )
    log_store.append_log_entry(
        orchestrator,
        build_step_resolution_entry(
            orchestrator, build_step(step_slug, actor=agent), timestamp="2026-01-01T00:01:00Z"
        ),
    )
    log_store.create_session_log(
        build_registration_entry(
            session_id,
            agent,
            parent_session_id=orchestrator,
            timestamp=registered_at,
        )
    )
    name = f"refs/harness/steps/{INSTANCE_ID}/{step_slug}/{session_id}"
    run_git(workspace, "update-ref", name, "HEAD", "")
    return name


def _conclude_step(log_store: SessionLogStore, orchestrator: str = ORCHESTRATOR_SESSION) -> None:
    """Journal the in-flight step's function-10 outcome in its orchestrator session."""
    log_store.append_log_entry(
        orchestrator,
        build_entry(
            "check-step-postconditions",
            "fail",
            session_id=orchestrator,
            workflow_instance_id=INSTANCE_ID,
            payload={
                "conditionChecks": [],
                "artifactDelivery": {
                    "artifact": ARTIFACT_KIND,
                    "artifactPath": None,
                    "outcome": "fail",
                    "failureMessage": "nothing delivered",
                },
                "promotion": {"outcome": "withheld", "workspaceCommit": "0" * 40},
            },
            timestamp="2026-01-01T00:05:00Z",
        ),
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
        """Function 8, invariant 2 + 4: the resource must resolve to ONE artifact schema
        identity, and "a layout in which two kinds claim one path is rejected at
        configuration load". This layout is therefore built directly, bypassing
        `ConfigLoader` — defence in depth for the branch the load-time rule is meant to
        make unreachable. Reached anyway, the checker denies it as an unresolvable
        resource (invariant 4) rather than authorizing by guesswork."""
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


def _authorize(
    log_store: SessionLogStore,
    access_control_list: AccessControlList,
    workspace_layout: WorkspaceLayout,
    artifact_store: ArtifactStore,
    *,
    session_id: str = SESSION,
    ref: str = ARTIFACT_REF,
    action: str = "create",
) -> AuthorizationReport:
    checker = _build_checker(log_store, access_control_list, workspace_layout, artifact_store)
    return checker.check_step_authorization(
        session_id=session_id,
        parent_session_id=None,
        artifact_path=Path(ref),
        action=action,
    )


def _lock_holder(workspace: Path, ref: str) -> str | None:
    """Read the step ref name an artifact lock names, or none when unlocked."""
    try:
        oid = run_git(workspace, "rev-parse", "--verify", "--quiet", f"refs/harness/locks/{ref}")
    except Exception:
        return None
    return run_git(workspace, "cat-file", "blob", oid.strip()).strip()


class TestStepInstanceGate:
    """Function 8, invariant 7: every artifact write happens inside a governed step instance."""

    def test_a_root_session_write_is_denied_as_no_step_in_flight(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """A write from an orchestrator (root) session is denied, naming "no step in
        flight" — and no lock is claimed."""
        _open_session(log_store, with_step=False)

        report = _authorize(log_store, access_control_list, workspace_layout, artifact_store)

        assert report.outcome.status == "denied"
        assert "no step in flight" in report.authorization.failure_message
        assert _lock_holder(workspace, ARTIFACT_REF) is None

    def test_a_worker_session_without_its_step_ref_is_denied(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """A worker session whose ref function 0 could not create is denied."""
        name = _open_session(log_store)
        run_git(workspace, "update-ref", "-d", name)

        report = _authorize(log_store, access_control_list, workspace_layout, artifact_store)

        assert report.outcome.status == "denied"
        assert "no step in flight" in report.authorization.failure_message

    def test_a_write_after_the_step_instances_outcome_is_denied(
        self, log_store, access_control_list, workspace_layout, artifact_store
    ) -> None:
        """A write after its step instance's function-10 outcome is denied."""
        _open_session(log_store)
        _conclude_step(log_store)

        report = _authorize(log_store, access_control_list, workspace_layout, artifact_store)

        assert report.outcome.status == "denied"
        assert "no step in flight" in report.authorization.failure_message


class TestArtifactLockClaim:
    """Function 8, invariant 8: writes are exclusive per artifact instance until promotion."""

    def test_an_allow_claims_the_paths_lock_for_the_acting_step_ref(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """A create-only claim to a blob naming the acting step ref."""
        name = _open_session(log_store)

        report = _authorize(log_store, access_control_list, workspace_layout, artifact_store)

        assert report.outcome.status == "allowed"
        assert _lock_holder(workspace, ARTIFACT_REF) == name

    def test_a_lock_another_step_instance_holds_denies_naming_its_holder(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """A lock held by any other step instance denies, the `failureMessage` naming
        the holder's workflow instance and step — and the lock is untouched."""
        holder = _open_session(log_store, session_id="01jholder", step_slug="draft")
        _authorize(
            log_store, access_control_list, workspace_layout, artifact_store, session_id="01jholder"
        )
        _open_session(log_store, orchestrator="01jother")

        report = _authorize(log_store, access_control_list, workspace_layout, artifact_store)

        assert report.outcome.status == "denied"
        assert "draft" in report.authorization.failure_message
        assert INSTANCE_ID in report.authorization.failure_message
        assert _lock_holder(workspace, ARTIFACT_REF) == holder

    def test_a_retry_inherits_and_re_points_the_previous_step_instances_lock(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """A lock held by an unpromoted earlier step instance of the same step in the
        same workflow instance is re-pointed to the retry by compare-and-swap — even
        though the path is dirty: for a held path the lock replaces the baseline check."""
        _open_session(log_store, session_id="01jprevious", registered_at="2026-01-01T00:01:10Z")
        _authorize(
            log_store, access_control_list, workspace_layout, artifact_store, session_id="01jprevious"
        )
        stage_artifact(workspace, ARTIFACT_REF, VALID_DOCUMENT)
        retry = _open_session(log_store, orchestrator="01jresumed")

        report = _authorize(
            log_store, access_control_list, workspace_layout, artifact_store, action="update"
        )

        assert report.outcome.status == "allowed"
        assert _lock_holder(workspace, ARTIFACT_REF) == retry

    def test_a_held_path_skips_the_baseline_check(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """Invariant 5: for a path the step instance holds, the lock replaces the
        baseline check — its working tree legitimately carries unpromoted bytes."""
        _open_session(log_store)
        _authorize(log_store, access_control_list, workspace_layout, artifact_store)
        stage_artifact(workspace, ARTIFACT_REF, VALID_DOCUMENT)

        report = _authorize(
            log_store, access_control_list, workspace_layout, artifact_store, action="update"
        )

        assert report.outcome.status == "allowed"

    def test_a_promoted_holders_lock_counts_as_released(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """A lock whose holder's tip is its own commit reachable from the workspace
        branch counts as released and is replaced by compare-and-swap."""
        _open_session(log_store, session_id="01jholder", step_slug="draft")
        _authorize(
            log_store, access_control_list, workspace_layout, artifact_store, session_id="01jholder"
        )
        holder = artifact_store.find_step_ref("01jholder")
        stage_artifact(workspace, ARTIFACT_REF, VALID_DOCUMENT)
        artifact_store.commit_artifacts((Path(ARTIFACT_REF),), session_id="01jholder", step_ref=holder)
        promoted = artifact_store.promote_step_ref(
            artifact_store.find_step_ref("01jholder"), run_git(workspace, "rev-parse", "HEAD").strip()
        )
        artifact_store.sync_index((ARTIFACT_REF,), promoted)
        mine = _open_session(log_store, orchestrator="01jother")

        report = _authorize(
            log_store, access_control_list, workspace_layout, artifact_store, action="update"
        )

        assert report.outcome.status == "allowed"
        assert _lock_holder(workspace, ARTIFACT_REF) == mine

    def test_a_deny_claims_no_lock(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """Postconditions: on a deny no lock is claimed or re-pointed."""
        _open_session(log_store, "product-owner")

        report = _authorize(log_store, access_control_list, workspace_layout, artifact_store)

        assert report.outcome.status == "denied"
        assert _lock_holder(workspace, ARTIFACT_REF) is None

    def test_a_superseded_step_instance_never_reclaims_its_retrys_lock(
        self, log_store, access_control_list, workspace_layout, artifact_store, workspace
    ) -> None:
        """Re-pointing runs from an EARLIER step instance to the one that continues it,
        never back: once a retry holds the lock, the orphaned step instance it
        continues — still without a function-10 outcome — is denied, naming the
        holder, and the retry's lock and bytes are untouched."""
        _open_session(log_store, session_id="01jprevious", registered_at="2026-01-01T00:01:10Z")
        _authorize(
            log_store, access_control_list, workspace_layout, artifact_store, session_id="01jprevious"
        )
        retry = _open_session(log_store, orchestrator="01jresumed")
        _authorize(log_store, access_control_list, workspace_layout, artifact_store, action="update")
        assert _lock_holder(workspace, ARTIFACT_REF) == retry

        report = _authorize(
            log_store,
            access_control_list,
            workspace_layout,
            artifact_store,
            session_id="01jprevious",
            action="update",
        )

        assert report.outcome.status == "denied"
        assert "artifact lock" in report.authorization.failure_message
        assert _lock_holder(workspace, ARTIFACT_REF) == retry
