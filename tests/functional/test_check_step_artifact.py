"""Functional tests for harness function 9, `check-step-artifact`.

Each test drives the assembled system through the real command entry point over a nested
workspace layout, validates both sides of the round trip against the function's own
contracts, and asserts what the commit gate left behind: the journal, the working tree,
and — the point of the whole function — the workspace's committed state.
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
    AMBIGUOUS_WORKSPACE_LAYOUT,
    EPIC_REF,
    FEATURE_REF,
    OTHER_EPIC_REF,
    OTHER_FEATURE_REF,
    UNBOUND_REF,
    build_write_boundary_harness,
    commit_write,
    epic_markdown,
    feature_json,
    is_worktree_clean,
    open_session,
    read_committed,
    read_last_commit_message,
    read_worktree,
    stage_write,
)

FUNCTION = "check-step-artifact"

HarnessBuilder = Callable[..., FunctionalHarness]


@pytest.fixture()
def harness(build_harness: HarnessBuilder) -> FunctionalHarness:
    """Answer the write-boundary rig: nested layout, one markdown kind, one JSON kind."""
    return build_write_boundary_harness(build_harness)


@pytest.fixture()
def builder_session(harness: FunctionalHarness) -> str:
    """Open the session the commit gate attributes its commit to."""
    return open_session(harness, "builder-session", "builder")


class TestCheckStepArtifact:
    """Function 9: the commit gate — validate the staged set, then commit or discard it."""

    def test_a_valid_set_commits_once_attributed_to_the_acting_session(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 9, invariant 3): 1 validated set = 1 commit, attributed to the
        acting session (its `sessionId` in the commit message) so Git history and the
        session log correlate — for the WHOLE set of one tool call, markdown and JSON
        alike."""
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))
        stage_write(harness, FEATURE_REF, feature_json("refunds", "approved"))
        commits_before = harness.count_commits()

        run = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPaths=[EPIC_REF, FEATURE_REF]
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "valid"}
        assert "artifactChecks" not in report
        assert harness.count_commits() == commits_before + 1
        assert f"session-id: {builder_session}" in read_last_commit_message(harness)
        assert harness.list_committed_paths() == (".gitignore", EPIC_REF, FEATURE_REF)
        assert_report_journaled_byte_identically(harness, run, 1)

    def test_a_markdown_artifact_is_validated_through_its_frontmatter(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 9, invariant 1 + worked example): a markdown artifact's schema
        binds its FRONTMATTER — the example reports `frontmatter.status: ...` — so an
        out-of-enum status is caught, not swallowed as unparsable prose."""
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "shipped"))

        run = harness.invoke(FUNCTION, sessionId=builder_session, artifactPaths=[EPIC_REF])

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "reverted"}
        check = report["artifactChecks"][0]
        assert check["artifactPath"] == EPIC_REF
        assert "/status" in check["failureMessage"]
        assert "shipped" in check["failureMessage"]
        assert harness.count_commits() == 1

    def test_the_harness_base_contract_is_in_force_through_the_schema_ref(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 9, invariant 1): artifact schemas extend the harness base
        contract via `$ref` — so the base's universal `slug` identity is enforced even
        though the kind's own schema declares no `slug` property at all."""
        stage_write(
            harness,
            EPIC_REF,
            "---\nslug: Checkout Epic\nstatus: draft\n---\n\n# checkout\n",
        )

        run = harness.invoke(FUNCTION, sessionId=builder_session, artifactPaths=[EPIC_REF])

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "reverted"}
        assert "/slug" in report["artifactChecks"][0]["failureMessage"]
        assert read_worktree(harness, EPIC_REF) is None

    def test_one_invalid_path_discards_the_whole_staged_set(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 9, invariant 2): call-level atomicity — any invalid path
        discards EVERY staged path of the call (tracked ones restored from `HEAD`, new
        ones deleted), and only the failing path is named: the valid siblings' revert is
        implied by set membership."""
        committed_epic = epic_markdown("billing", "draft")
        commit_write(harness, OTHER_EPIC_REF, committed_epic)
        stage_write(harness, OTHER_EPIC_REF, epic_markdown("billing", "approved"))
        stage_write(harness, FEATURE_REF, feature_json("refunds", "draft"))
        stage_write(harness, OTHER_FEATURE_REF, feature_json("invoicing", "shipped"))
        commits_before = harness.count_commits()

        run = harness.invoke(
            FUNCTION,
            sessionId=builder_session,
            artifactPaths=[OTHER_EPIC_REF, FEATURE_REF, OTHER_FEATURE_REF],
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "reverted"}
        assert [check["artifactPath"] for check in report["artifactChecks"]] == [
            OTHER_FEATURE_REF
        ]
        assert report["artifactChecks"][0]["revert"] == {"action": "deleted"}
        assert read_worktree(harness, OTHER_EPIC_REF) == committed_epic
        assert read_worktree(harness, FEATURE_REF) is None
        assert read_worktree(harness, OTHER_FEATURE_REF) is None
        assert harness.count_commits() == commits_before
        assert is_worktree_clean(harness)

    def test_a_failing_tracked_path_is_restored_from_head(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 9, invariant 2 + output contract): a tracked path's discard is
        a restore from `HEAD`, recorded as such in the same validation entry — there is
        no second revert entry."""
        committed = epic_markdown("checkout", "draft")
        commit_write(harness, EPIC_REF, committed)
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "shipped"))

        run = harness.invoke(FUNCTION, sessionId=builder_session, artifactPaths=[EPIC_REF])

        report = assert_contract_round_trip(harness, run)
        assert report["artifactChecks"][0]["revert"] == {
            "action": "restored",
            "from": "HEAD",
        }
        assert read_worktree(harness, EPIC_REF) == committed
        assert harness.list_journaled_functions(builder_session) == ("start-session", FUNCTION)

    def test_committed_state_never_holds_the_invalid_bytes(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (C6 + Workspace Git plane 2): committed state never holds invalid bytes —
        transactional by construction. The rejected write leaves `HEAD` exactly as the
        last validated commit left it."""
        committed = epic_markdown("checkout", "approved")
        commit_write(harness, EPIC_REF, committed)
        committed_before = harness.list_committed_paths()
        commits_before = harness.count_commits()
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "shipped"))

        harness.invoke(FUNCTION, sessionId=builder_session, artifactPaths=[EPIC_REF])

        assert read_committed(harness, EPIC_REF) == committed
        assert harness.list_committed_paths() == committed_before
        assert harness.count_commits() == commits_before

    def test_a_byte_identical_set_validates_vacuously_and_commits_nothing(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 9, invariant 4): a staged path byte-identical to `HEAD`
        validates vacuously and stages nothing — `valid` asserts validity, not that a
        commit occurred."""
        commit_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))
        commits_before = harness.count_commits()

        run = harness.invoke(FUNCTION, sessionId=builder_session, artifactPaths=[EPIC_REF])

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "valid"}
        assert harness.count_commits() == commits_before

    def test_a_path_bound_to_no_schema_discards_the_set_defensively(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 9, precondition E): a path resolving to no artifact schema is
        `state-error` (`artifact-schema-unresolved`), journaled — and the whole staged
        set is discarded DEFENSIVELY, siblings included."""
        stage_write(harness, FEATURE_REF, feature_json("refunds", "draft"))
        stage_write(harness, UNBOUND_REF, "loose prose nothing binds\n")
        commits_before = harness.count_commits()

        run = harness.invoke(
            FUNCTION, sessionId=builder_session, artifactPaths=[FEATURE_REF, UNBOUND_REF]
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "artifact-schema-unresolved"
        assert read_worktree(harness, FEATURE_REF) is None
        assert read_worktree(harness, UNBOUND_REF) is None
        assert harness.count_commits() == commits_before
        assert assert_journal_contract(harness, builder_session)[-1]["report"] == report

    def test_a_path_two_kinds_bind_is_refused_never_guessed(
        self, build_harness: HarnessBuilder
    ) -> None:
        """Spec (function 9, invariant 1 — `type` disambiguation): when several path
        patterns match, the kind must be disambiguated by the artifact's `type`. Neither
        write-boundary contract carries one, so the harness cannot disambiguate and
        refuses instead of guessing a schema: `artifact-schema-unresolved`, the set
        discarded, committed state untouched."""
        harness = build_write_boundary_harness(build_harness, AMBIGUOUS_WORKSPACE_LAYOUT)
        session = open_session(harness, "builder-session", "builder")
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))

        run = harness.invoke(FUNCTION, sessionId=session, artifactPaths=[EPIC_REF])

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "artifact-schema-unresolved"
        assert "ambiguous" in report["outcome"]["error"]["message"]
        assert read_worktree(harness, EPIC_REF) is None
        assert harness.count_commits() == 1

    def test_each_validation_journals_exactly_one_entry(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (function 9, Postconditions): ONE log entry per write validation
        (`valid` / `reverted`), covering the whole set — the revert records ride inside
        the validation entry, never in a second one."""
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))
        harness.invoke(FUNCTION, sessionId=builder_session, artifactPaths=[EPIC_REF])
        stage_write(harness, FEATURE_REF, feature_json("refunds", "shipped"))

        harness.invoke(FUNCTION, sessionId=builder_session, artifactPaths=[FEATURE_REF])

        entries = assert_journal_contract(harness, builder_session)
        assert harness.list_journaled_functions(builder_session) == (
            "start-session",
            FUNCTION,
            FUNCTION,
        )
        assert [entry["report"]["outcome"]["status"] for entry in entries] == [
            "started",
            "valid",
            "reverted",
        ]
        assert len(entries[2]["report"]["artifactChecks"]) == 1

    def test_an_inquiry_naming_no_path_fails_at_the_command_exit_plane(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (rule 4 + the input contract's `minItems`): an empty write set fails
        contract validation, which is pre-attribution — no report at all, stderr plus a
        nonzero exit, nothing journaled and nothing committed."""
        assert harness.validate_inquiry(FUNCTION, {"sessionId": builder_session}) != ()
        commits_before = harness.count_commits()

        run = harness.invoke_entry_shim(
            FUNCTION, sessionId=builder_session, artifactPaths=[]
        )

        assert run.report is None
        assert run.exit_code == 1
        assert "invalid-inquiry" in run.stderr
        assert harness.list_journaled_functions(builder_session) == ("start-session",)
        assert harness.count_commits() == commits_before

    def test_an_unregistered_session_fails_closed_without_a_log(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): `session-unregistered` returns its report but has no log to
        journal to — and the commit gate never opens for it."""
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))

        run = harness.invoke(FUNCTION, sessionId="ghost-session", artifactPaths=[EPIC_REF])

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "inquiry-error"
        assert run.error_code == "session-unregistered"
        assert not harness.is_session_logged("ghost-session")
        assert harness.count_commits() == 1

    def test_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, builder_session: str
    ) -> None:
        """Spec (rule 3, C8): the refusal is `state-error` (`session-ended`), never
        journaled — and no staged write is promoted on the way out."""
        stage_write(harness, EPIC_REF, epic_markdown("checkout", "draft"))
        harness.invoke("end-session", sessionId=builder_session)
        before = harness.list_journaled_functions(builder_session)

        run = harness.invoke(FUNCTION, sessionId=builder_session, artifactPaths=[EPIC_REF])

        assert_contract_round_trip(harness, run)
        assert run.status == "state-error"
        assert run.error_code == "session-ended"
        assert harness.list_journaled_functions(builder_session) == before
        assert harness.count_commits() == 1
