"""Functional tests for harness function 2, `resolve-workflow-skills`.

Each test drives the assembled system through the real command entry point, validates
both sides of the round trip against the function's own contracts, and asserts the
journal the invocation left behind.
"""

from __future__ import annotations

from functional_fixtures import (
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
)

FUNCTION = "resolve-workflow-skills"

# The default rig's two workflows, in catalog order, deduplicated first-seen.
EXPECTED_SKILLS = ["workflow-selection", "planning-procedure", "verification-procedure"]


class TestResolveWorkflowSkills:
    """Function 2: the orchestrator's procedure skills, from configuration alone."""

    def test_orchestrator_session_resolves_its_declared_skill_ids(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 2, Interface + invariant 1): `resolved` plus the selection
        skill and each facilitated workflow's procedure skill — configuration only."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "resolved"}
        assert report["skills"] == EXPECTED_SKILLS

    def test_the_invocation_journals_alongside_function_ones_entry(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 2, Postconditions): the invocation appends its own entry to
        the session's log, alongside function 1's."""
        harness.invoke("resolve-workflow-instructions", sessionId=orchestrator_session)
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        entries = assert_journal_contract(harness, orchestrator_session)
        assert harness.list_journaled_functions(orchestrator_session) == (
            "start-session",
            "resolve-workflow-instructions",
            FUNCTION,
        )
        assert entries[2]["report"] == run.report
        assert_report_journaled_byte_identically(harness, run, 2)

    def test_resolution_is_deterministic(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 2, invariant 2): resolution is deterministic — the
        configuration decides, never the agent."""
        first = harness.invoke(FUNCTION, sessionId=orchestrator_session)
        second = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert assert_contract_round_trip(harness, second)["skills"] == first.report["skills"]

    def test_a_step_session_is_refused_as_the_wrong_session_kind(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 2, Preconditions): function 1's preconditions and violation
        outcomes apply identically — a step session is `state-error`
        (`session-kind-mismatch`), journaled."""
        harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")
        harness.invoke(
            "start-session",
            sessionId="step-session",
            parentSessionId=orchestrator_session,
            agent="builder",
        )

        run = harness.invoke(FUNCTION, sessionId="step-session")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "session-kind-mismatch"
        assert assert_journal_contract(harness, "step-session")[-1]["report"] == report

    def test_an_unregistered_session_is_reported_but_never_journaled(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): `session-unregistered` returns its report but has no log to
        journal to."""
        run = harness.invoke(FUNCTION, sessionId="ghost-session")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "inquiry-error"
        assert run.error_code == "session-unregistered"
        assert not harness.is_session_logged("ghost-session")

    def test_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 3, C8): the C8 refusal is `state-error` (`session-ended`) and no
        entry ever follows the ending entry."""
        harness.invoke("end-session", sessionId=orchestrator_session)
        before = harness.list_journaled_functions(orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert_contract_round_trip(harness, run)
        assert run.status == "state-error"
        assert run.error_code == "session-ended"
        assert harness.list_journaled_functions(orchestrator_session) == before

    def test_a_malformed_inquiry_produces_no_report_at_the_exit_plane(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): a contract-validation failure produces no report at all and
        surfaces at the command exit plane."""
        run = harness.invoke(FUNCTION, sessionId="NOT-A-SLUG")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert run.stderr.strip() != ""
