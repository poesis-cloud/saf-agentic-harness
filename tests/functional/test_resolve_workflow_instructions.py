"""Functional tests for harness function 1, `resolve-workflow-instructions`.

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

FUNCTION = "resolve-workflow-instructions"

# The default rig's two workflows, in catalog order, deduplicated first-seen.
EXPECTED_INSTRUCTIONS = [
    "workflow-selection-handling",
    "step-resolution-handling",
    "no-next-step-handling",
]


def _open_step_session(harness: FunctionalHarness, orchestrator_session: str) -> str:
    """Dispatch the planning workflow's first step and register the acting session."""
    harness.invoke("resolve-step", sessionId=orchestrator_session, workflowSlug="planning")
    harness.invoke(
        "start-session",
        sessionId="step-session",
        parentSessionId=orchestrator_session,
        agent="builder",
    )
    return "step-session"


class TestResolveWorkflowInstructions:
    """Function 1: the orchestrator's workflow guidance, from configuration alone."""

    def test_orchestrator_session_resolves_its_declared_instruction_refs(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 1, Interface + invariants 1 and 3): `resolved` plus the
        instruction refs the session's orchestrator declares — keyed by orchestrator,
        decided by configuration, never by the agent."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "resolved"}
        assert report["instructions"] == EXPECTED_INSTRUCTIONS
        assert report["context"]["workflowInstanceId"] is None

    def test_the_invocation_appends_its_own_entry(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 1, Postconditions): the invocation appends its own entry to
        the session's log — 1 invocation = 1 entry, after function 0's."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        entries = assert_journal_contract(harness, orchestrator_session)
        assert harness.list_journaled_functions(orchestrator_session) == (
            "start-session",
            FUNCTION,
        )
        assert entries[1]["report"] == run.report
        assert_report_journaled_byte_identically(harness, run, 1)

    def test_resolution_is_deterministic_across_repeated_boundaries(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 1, invariant 3 + Boundary Normalization): re-resolution at
        every session-started boundary is mandatory and deterministic — same refs,
        one more entry."""
        first = harness.invoke(FUNCTION, sessionId=orchestrator_session)
        second = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert assert_contract_round_trip(harness, second)["instructions"] == (
            first.report["instructions"]
        )
        assert len(harness.read_log(orchestrator_session)) == 3

    def test_a_step_session_is_refused_as_the_wrong_session_kind(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 1, precondition E): a session an unresolved `resolve-step`
        entry correlates to is a STEP session and loads functions 6-7 instead —
        violation `state-error` (`session-kind-mismatch`), journaled."""
        step_session = _open_step_session(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=step_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "session-kind-mismatch"
        entries = assert_journal_contract(harness, step_session)
        assert entries[-1]["report"] == report

    def test_an_unregistered_session_is_reported_but_never_journaled(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 1, precondition E + rule 4): `session-unregistered` returns
        its report — the context is constructible — but has no log to journal to."""
        run = harness.invoke(FUNCTION, sessionId="ghost-session")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "inquiry-error"
        assert run.error_code == "session-unregistered"
        assert not harness.is_session_logged("ghost-session")

    def test_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 3, C8): a call against a session whose log carries an ending
        entry is `state-error` (`session-ended`) and is never journaled."""
        harness.invoke("end-session", sessionId=orchestrator_session)
        before = harness.list_journaled_functions(orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "session-ended"
        assert harness.list_journaled_functions(orchestrator_session) == before

    def test_a_malformed_inquiry_produces_no_report_at_the_exit_plane(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): an inquiry its own contract rejects produces no report — it
        surfaces at the command exit plane with stderr and a nonzero exit."""
        run = harness.invoke(FUNCTION, sessionId="Not_A_Slug")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert "invalid-inquiry" in run.stderr
