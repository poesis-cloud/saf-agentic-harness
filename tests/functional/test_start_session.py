"""Functional tests for harness function 0, `start-session`.

Every test drives the assembled system through the real command entry point and
asserts the contract-validated In -> Out round trip, the postconditions on the real
workspace, and the invariants observable from outside.
"""

from __future__ import annotations

import pytest

from functional_fixtures import (
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
)

FUNCTION = "start-session"


class TestStartSession:
    """Function 0: the session seed and the very file every later entry appends to."""

    def test_root_session_registers_and_creates_its_log(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, Interface + Postconditions): `started` + the `session`
        object, and `<workspace>/logs/<sessionId>.log.jsonl` exists with the start as
        its first line."""
        run = harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "started"}
        assert report["session"] == {
            "agent": "orchestrator",
            "sessionId": "root-session",
            "parentSessionId": None,
        }
        assert report["context"] == {
            "function": FUNCTION,
            "sessionId": "root-session",
            "parentSessionId": None,
            "workflowInstanceId": None,
        }
        assert harness.log_path("root-session").is_file()
        entries = assert_journal_contract(harness, "root-session")
        assert len(entries) == 1
        assert_report_journaled_byte_identically(harness, run, 0)

    def test_registration_precedes_every_other_entry(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, invariant 1): starting precedes everything — function 0
        creates the very file the others append to, so its entry is line one."""
        harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")
        harness.invoke("resolve-workflow-skills", sessionId="root-session")

        assert harness.list_journaled_functions("root-session") == (
            FUNCTION,
            "resolve-workflow-skills",
        )

    def test_root_session_of_a_non_framework_agent_passes_through_unstarted(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, precondition E + rule 2): a root `agent` naming no
        framework agent is `not-applicable` — returned, never journaled, so no log
        file is created at all."""
        run = harness.invoke(FUNCTION, sessionId="stranger-session", agent="stranger")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "not-applicable"}
        assert "session" not in report
        assert not harness.is_session_logged("stranger-session")

    def test_redelivery_replays_the_registration_without_a_second_entry(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, invariant 4): re-delivery of the same start for an
        already-registered OPEN session returns the same `started` report and appends
        no second registration."""
        first = harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")
        second = harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")

        replayed = assert_contract_round_trip(harness, second)
        assert replayed == first.report
        assert len(harness.read_log("root-session")) == 1

    def test_reregistration_under_another_agent_conflicts_and_journals(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 0, invariant 4): a re-registration naming a DIFFERENT agent
        for a registered session is `state-error` (`session-conflict`), journaled —
        identity never silently mutates."""
        harness.invoke(FUNCTION, sessionId="root-session", agent="orchestrator")
        run = harness.invoke(FUNCTION, sessionId="root-session", agent="builder")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "session-conflict"
        entries = assert_journal_contract(harness, "root-session")
        assert len(entries) == 2
        assert entries[1]["report"] == report

    def test_step_session_without_a_correlated_resolution_passes_through(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, precondition E + rule 2): for a step session the
        correlation IS the check — a dispatch no step resolution correlates to finds
        no target and is `not-applicable`, unjournaled."""
        run = harness.invoke(
            FUNCTION,
            sessionId="step-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "not-applicable"}
        assert not harness.is_session_logged("step-session")

    def test_step_session_registers_against_its_parents_resolution(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, invariant 3): the parent chain is unbounded — each start
        records one parent, and a step session correlating to its parent's unresolved
        step resolution registers with that parent recorded."""
        harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")

        run = harness.invoke(
            FUNCTION,
            sessionId="step-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "started"}
        assert report["session"] == {
            "agent": "builder",
            "sessionId": "step-session",
            "parentSessionId": orchestrator_session,
        }
        assert len(assert_journal_contract(harness, "step-session")) == 1

    def test_start_against_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 0, invariant 4 + rule 3): a start against an ENDED id is the
        C8 refusal — `state-error` (`session-ended`) — and no entry ever follows the
        ending entry."""
        harness.invoke("end-session", sessionId=orchestrator_session)
        before = harness.list_journaled_functions(orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session, agent="orchestrator")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "session-ended"
        assert harness.list_journaled_functions(orchestrator_session) == before

    def test_a_malformed_inquiry_produces_no_report_at_the_exit_plane(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): a contract-validation failure produces NO report at all — it
        surfaces at the command exit plane with stderr and a nonzero exit, and nothing
        is journaled."""
        run = harness.invoke(FUNCTION, sessionId="root-session")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert "invalid-inquiry" in run.stderr
        assert not harness.is_session_logged("root-session")

    @pytest.mark.parametrize("session_id", ["Root_Session", "root session", "../escape"])
    def test_an_unsafe_session_id_never_becomes_a_log_filename(
        self, harness: FunctionalHarness, session_id: str
    ) -> None:
        """Spec (function 0, precondition E; Logging, sanitization): the id becomes a
        log filename, so a raw host id is a path-traversal vector the contract
        rejects, unjournalably."""
        run = harness.invoke(FUNCTION, sessionId=session_id, agent="orchestrator")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert not (harness.workspace_dir / "logs").exists()

    def test_the_entry_shim_runs_the_same_registration(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (Invocation surfaces): every harness function is exposed as ONE harness
        command — `harness.py <function> [flags]` over `FRAMEWORK_DIR` is that surface."""
        run = harness.invoke_entry_shim(
            FUNCTION, sessionId="shim-session", agent="orchestrator"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "started"}
        assert len(assert_journal_contract(harness, "shim-session")) == 1
