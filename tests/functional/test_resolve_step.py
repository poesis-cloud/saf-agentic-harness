"""Functional tests for harness function 3, `resolve-step`.

Each test drives the assembled system through the real command entry point, validates
both sides of the round trip against the function's own contracts, and asserts the
journal and the untouched workspace the invocation left behind.
"""

from __future__ import annotations

import re

from functional_fixtures import (
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
)

FUNCTION = "resolve-step"

# Logging: a minted instance id is its workflow slug plus an uppercase Crockford-base32
# mint of at least four characters — the prefix is load-bearing for the deduction.
INSTANCE_ID_PATTERN = re.compile(r"^planning-[0-9A-HJKMNP-TV-Z]{4,}$")


def _execute_in_flight_step(harness: FunctionalHarness, session_id: str) -> None:
    """Journal the in-flight step's passing outcome, so the cursor may move on."""
    harness.invoke("check-step-postconditions", sessionId=session_id)


class TestResolveStep:
    """Function 3: the resolution core — the harness alone governs sequencing."""

    def test_first_resolution_opens_an_instance_and_returns_the_configured_step(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 3, Interface + Postconditions): a `step-resolution` carries
        the configured step verbatim, the minted `workflowInstanceId` surfaces
        read-only in `context`, and the opening IS this invocation's own entry."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session, workflowSlug="planning")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "step-resolution"}
        assert report["step"] == {
            "slug": "draft",
            "actor": "builder",
            "artifact": "report",
            "instructions": ["draft-guidance"],
            "capabilities": {
                "deep-reasoning": 0.0,
                "coding": 8.0,
                "tool-use": 0.0,
                "long-context": 0.0,
                "multimodal": 0.0,
                "writing-quality": 0.0,
                "instruction-following": 0.0,
                "fast-iteration": 0.0,
                "schema-adherence": 0.0,
            },
            "skills": ["drafting"],
        }
        assert INSTANCE_ID_PATTERN.match(report["context"]["workflowInstanceId"])
        entries = assert_journal_contract(harness, orchestrator_session)
        assert harness.list_journaled_functions(orchestrator_session) == (
            "start-session",
            FUNCTION,
        )
        assert entries[1]["report"] == report
        assert_report_journaled_byte_identically(harness, run, 1)

    def test_resolution_writes_nothing_beyond_its_log_entry(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 3, invariant 4 + Postconditions): resolution never writes
        artifacts and never starts the step — nothing beyond the log entry changes."""
        commits_before = harness.count_commits()

        harness.invoke(FUNCTION, sessionId=orchestrator_session, workflowSlug="planning")

        assert harness.count_commits() == commits_before
        assert harness.list_committed_paths() == (".gitignore",)

    def test_the_next_resolution_continues_the_same_instance_in_authored_order(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 3, invariants 1, 2 and 8): the cursor derives from the
        journaled outcomes — once `draft` is journaled executed the same instance
        resolves the first remaining step whose predecessors are all executed."""
        first = harness.invoke(
            FUNCTION, sessionId=orchestrator_session, workflowSlug="planning"
        )
        _execute_in_flight_step(harness, orchestrator_session)

        second = harness.invoke(
            FUNCTION, sessionId=orchestrator_session, workflowSlug="planning"
        )

        report = assert_contract_round_trip(harness, second)
        assert report["step"]["slug"] == "review"
        assert report["context"]["workflowInstanceId"] == (
            first.report["context"]["workflowInstanceId"]
        )

    def test_every_step_executed_answers_no_next_step(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 3, invariant 5): `no-next-step` only observes that every
        authored step currently has a passing journaled execution — no step
        attached, and never a completion verdict."""
        harness.invoke(FUNCTION, sessionId=orchestrator_session, workflowSlug="planning")
        _execute_in_flight_step(harness, orchestrator_session)
        harness.invoke(FUNCTION, sessionId=orchestrator_session, workflowSlug="planning")
        _execute_in_flight_step(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session, workflowSlug="planning")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "no-next-step"}
        assert "step" not in report

    def test_a_call_arriving_while_a_step_is_in_flight_is_refused(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 3, invariant 9): between a step's resolution and its
        journaled outcome no other step resolves — the call is refused
        (`state-error`, `step-in-flight`), journaled, never silently re-resolved."""
        harness.invoke(FUNCTION, sessionId=orchestrator_session, workflowSlug="planning")

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session, workflowSlug="planning")

        report = assert_contract_round_trip(harness, run)
        assert run.status == "state-error"
        assert run.error_code == "step-in-flight"
        entries = assert_journal_contract(harness, orchestrator_session)
        assert entries[-1]["report"] == report

    def test_an_unknown_workflow_slug_is_refused_and_journaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 3, precondition E): `workflowSlug` is the one agent-supplied
        parameter, so its domain validation is runtime — violation `inquiry-error`
        (`unknown-workflow`), journaled."""
        run = harness.invoke(
            FUNCTION, sessionId=orchestrator_session, workflowSlug="nonexistent"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "inquiry-error"
        assert run.error_code == "unknown-workflow"
        assert assert_journal_contract(harness, orchestrator_session)[-1]["report"] == report

    def test_a_session_agent_that_does_not_facilitate_is_refused_and_journaled(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 3, precondition E): the session's agent must be the named
        workflow's facilitator — violation `inquiry-error` (`not-facilitator`),
        journaled."""
        harness.invoke("start-session", sessionId="builder-session", agent="builder")

        run = harness.invoke(FUNCTION, sessionId="builder-session", workflowSlug="planning")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "inquiry-error"
        assert run.error_code == "not-facilitator"
        assert assert_journal_contract(harness, "builder-session")[-1]["report"] == report

    def test_an_unregistered_session_fails_closed_without_a_log(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 3, precondition E; mediated-invocation backstop): an id that
        resolves to no registered session is rejected outright — `inquiry-error`
        (`session-unregistered`), unjournalable (rule 4)."""
        run = harness.invoke(FUNCTION, sessionId="ghost-session", workflowSlug="planning")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "inquiry-error"
        assert run.error_code == "session-unregistered"
        assert not harness.is_session_logged("ghost-session")

    def test_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 3, C8): a call against an ended session is `state-error`
        (`session-ended`) and no entry ever follows the ending entry."""
        harness.invoke("end-session", sessionId=orchestrator_session)
        before = harness.list_journaled_functions(orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session, workflowSlug="planning")

        assert_contract_round_trip(harness, run)
        assert run.status == "state-error"
        assert run.error_code == "session-ended"
        assert harness.list_journaled_functions(orchestrator_session) == before

    def test_an_inquiry_without_a_workflow_slug_produces_no_report(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 4): the inquiry fails its own input contract, so no
        contract-valid report can be built — stderr and a nonzero exit instead."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert harness.list_journaled_functions(orchestrator_session) == ("start-session",)
