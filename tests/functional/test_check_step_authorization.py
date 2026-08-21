"""Functional tests for harness function 8, `check-step-authorization`.

Each test drives the assembled system through the real command entry point over a nested
workspace layout, validates both sides of the round trip against the function's own
contracts, and asserts the journaled decision plus the artifact plane the invocation left
untouched — function 8 decides a write, it never performs one.
"""

from __future__ import annotations

from typing import Callable

import pytest

from functional_fixtures import (
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
)
from write_boundary_rig import (
    EPIC_REF,
    FEATURE_REF,
    LOGS_REF,
    UNBOUND_REF,
    build_write_boundary_harness,
    commit_write,
    epic_markdown,
    open_session,
    stage_write,
)

FUNCTION = "check-step-authorization"

HarnessBuilder = Callable[..., FunctionalHarness]


@pytest.fixture()
def harness(build_harness: HarnessBuilder) -> FunctionalHarness:
    """Answer the write-boundary rig: nested layout, two artifact kinds, three actors."""
    return build_write_boundary_harness(build_harness)


@pytest.fixture()
def builder_session(harness: FunctionalHarness) -> str:
    """Open the session of `builder`, the agent holding every epic privilege."""
    return open_session(harness, "builder-session", "builder")


class TestCheckStepAuthorization:
    """Function 8: the live RBAC and staging-baseline gate in front of every write."""

    def test_a_granted_write_is_allowed_naming_the_resolved_resource(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, Interface + invariant 2): an allow is a decision about a
        RESOLVED resource — the artifact's schema identity deduced from the nested write
        path — journaled as one entry, byte for byte the report the caller received."""
        run = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=EPIC_REF, action="create"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "allowed"}
        assert report["authorization"] == {
            "actor": "builder",
            "artifactPath": EPIC_REF,
            "action": "create",
            "resource": "epic",
        }
        entries = assert_journal_contract(harness, builder_session)
        assert entries[-1]["report"] == report
        assert_report_journaled_byte_identically(harness, run, 1)

    def test_the_actor_is_the_registered_session_agent_never_an_input(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, invariant 1): the actor is the AGENT the session registered,
        never a function input — the contract has no property to supply one, and one
        identical inquiry decides oppositely under two differently-registered
        sessions."""
        assert harness.validate_inquiry(
            FUNCTION,
            {
                "sessionId": builder_session,
                "artifactPath": EPIC_REF,
                "action": "update",
                "actor": "builder",
            },
        ) != ()
        reviewer_session = open_session(harness, "reviewer-session", "reviewer")

        allowed = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=EPIC_REF, action="update"
        )
        denied = harness.invoke(
            FUNCTION, sessionId=reviewer_session, artifactPath=EPIC_REF, action="update"
        )

        allowed_report = assert_contract_round_trip(harness, allowed)
        denied_report = assert_contract_round_trip(harness, denied)
        assert allowed_report["authorization"]["actor"] == "builder"
        assert allowed.status == "allowed"
        assert denied.status == "denied"
        assert denied_report["authorization"]["actor"] == "reviewer"

    def test_a_property_suffix_is_ignored_and_echoed(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, invariant 3): authorization is whole-resource — a
        `#property` suffix is ignored when resolving, when deciding, AND when judging the
        staging baseline of invariant 5, while the report echoes the path the host
        actually asked about."""
        run = harness.invoke(
            FUNCTION,
            sessionId=builder_session,
            artifactPath=f"{EPIC_REF}#status",
            action="update",
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "allowed"}
        assert report["authorization"]["resource"] == "epic"
        assert report["authorization"]["artifactPath"] == f"{EPIC_REF}#status"

        commit_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "approved"))

        on_dirty_resource = harness.invoke(
            FUNCTION,
            sessionId=builder_session,
            artifactPath=f"{EPIC_REF}#status",
            action="update",
        )

        dirty_report = assert_contract_round_trip(harness, on_dirty_resource)
        assert dirty_report["outcome"] == {"status": "denied"}
        assert "unclean staging baseline" in dirty_report["authorization"]["failureMessage"]

    def test_a_write_nobody_granted_is_denied_naming_the_missing_privilege(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 8, invariant 4 + "no implicit grants"): a write no role of the
        acting agent grants is an ordinary journaled `denied`, its `failureMessage`
        naming the missing privilege over the resolved resource."""
        session = open_session(harness, "reviewer-session", "reviewer")

        run = harness.invoke(
            FUNCTION, sessionId=session, artifactPath=EPIC_REF, action="create"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "denied"}
        assert report["authorization"]["resource"] == "epic"
        assert "missing privilege" in report["authorization"]["failureMessage"]
        assert "create epic" in report["authorization"]["failureMessage"]
        assert assert_journal_contract(harness, session)[-1]["report"] == report

    def test_a_granted_delete_is_denied_as_a_forward_declaration(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, ACL principles): `delete` is a forward declaration — roles
        MAY grant it and this function denies it unconditionally until enforcement
        downstream is modelled. `builder` holds `delete epic`; the deny is not about the
        grant."""
        run = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=EPIC_REF, action="delete"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "denied"}
        assert report["authorization"]["resource"] == "epic"
        assert "unsupported action" in report["authorization"]["failureMessage"]

    def test_an_unresolvable_path_is_denied_without_a_resource(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, invariant 4 + output contract): a path the layout binds to
        no artifact is denied, and the decision carries NO `resource` — naming one would
        be indistinguishable from a real answer."""
        run = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=UNBOUND_REF, action="create"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "denied"}
        assert "resource" not in report["authorization"]
        assert "unresolvable resource" in report["authorization"]["failureMessage"]

    def test_the_logs_path_is_denied_for_every_actor(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, invariant 6): a write targeting the workspace logs path is
        denied ALWAYS, for every actor — logs are harness-authored and single-writer
        (C0), and the ACL vocabulary holds no resource that could grant them."""
        facilitator_session = open_session(harness, "orchestrator-session", "orchestrator")

        for session in (builder_session, facilitator_session):
            run = harness.invoke(
                FUNCTION, sessionId=session, artifactPath=LOGS_REF, action="create"
            )

            report = assert_contract_round_trip(harness, run)
            assert report["outcome"] == {"status": "denied"}
            assert "resource" not in report["authorization"]
            assert "logs path" in report["authorization"]["failureMessage"]

    def test_a_dirty_tracked_target_is_denied_on_its_staging_baseline(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, invariant 5): a write whose staging baseline is not clean
        against `HEAD` is denied at the same boundary — so the staged write is always the
        only staged content at its path (C6)."""
        commit_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "approved"))

        run = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=EPIC_REF, action="update"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "denied"}
        assert report["authorization"]["resource"] == "epic"
        assert "unclean staging baseline" in report["authorization"]["failureMessage"]

    def test_a_pre_existing_untracked_target_is_denied_on_its_staging_baseline(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, invariant 5): a pre-existing UNTRACKED target is unclean
        too — the bypassed bytes it holds never become the baseline of a mediated
        write."""
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))

        run = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=EPIC_REF, action="create"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "denied"}
        assert "unclean staging baseline" in report["authorization"]["failureMessage"]

    def test_every_decision_journals_one_entry_and_touches_no_artifact(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 8, Postconditions): ONE log entry per authorization decision,
        allow and deny alike — and on a deny the write never lands, so the artifact plane
        is exactly as it was."""
        commit_write(harness, FEATURE_REF, '{"slug": "refunds", "status": "draft"}\n')
        committed_before = harness.list_committed_paths()
        commits_before = harness.count_commits()

        harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=EPIC_REF, action="create"
        )
        harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=FEATURE_REF, action="update"
        )

        assert harness.list_journaled_functions(builder_session) == (
            "start-session",
            FUNCTION,
            FUNCTION,
        )
        entries = harness.read_log(builder_session)
        assert [entry["report"]["outcome"]["status"] for entry in entries] == [
            "started",
            "allowed",
            "denied",
        ]
        assert harness.count_commits() == commits_before
        assert harness.list_committed_paths() == committed_before
        assert not (harness.workspace_dir / EPIC_REF).exists()

    def test_an_action_outside_the_vocabulary_fails_at_the_command_exit_plane(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (rule 4 + the input contract's `action` enum): an action the contract
        does not name fails CONTRACT validation, which is pre-attribution — no report at
        all, stderr plus a nonzero exit, and nothing journaled. The inline
        `unknown-action` precondition can therefore never be reached through the command
        boundary: the enum refuses first."""
        assert harness.validate_inquiry(
            FUNCTION,
            {"sessionId": builder_session, "artifactPath": EPIC_REF, "action": "append"},
        ) != ()

        run = harness.invoke_entry_shim(
            FUNCTION, sessionId=builder_session, artifactPath=EPIC_REF, action="append"
        )

        assert run.report is None
        assert run.exit_code == 1
        assert "invalid-inquiry" in run.stderr
        assert harness.list_journaled_functions(builder_session) == ("start-session",)

    def test_an_unregistered_session_fails_closed_without_a_log(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): `session-unregistered` returns its report — the context is
        constructible — but has no log to journal to."""
        run = harness.invoke(
            FUNCTION, sessionId="ghost-session", artifactPath=EPIC_REF, action="create"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "inquiry-error"
        assert run.error_code == "session-unregistered"
        assert not harness.is_session_logged("ghost-session")

    def test_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (rule 3, C8): a session-bound call against an ended session is
        `state-error` (`session-ended`) and no entry ever follows the ending entry."""
        harness.invoke("end-session", sessionId=builder_session)
        before = harness.list_journaled_functions(builder_session)

        run = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPath=EPIC_REF, action="create"
        )

        assert_contract_round_trip(harness, run)
        assert run.status == "state-error"
        assert run.error_code == "session-ended"
        assert harness.list_journaled_functions(builder_session) == before
