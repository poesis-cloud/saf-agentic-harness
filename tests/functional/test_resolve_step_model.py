"""Functional tests for harness function 4, `resolve-step-model`.

Each test drives the assembled system through the real command entry point, validates
both sides of the round trip against the function's own contracts, and asserts the
journal the invocation left behind.
"""

from __future__ import annotations

from typing import Callable

from functional_fixtures import (
    TIED_MODEL_PROFILES,
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
)

FUNCTION = "resolve-step-model"


def _dispatch_draft(harness: FunctionalHarness, session_id: str) -> None:
    """Put the planning workflow's first step, `draft`, in flight."""
    harness.invoke("resolve-step", sessionId=session_id, workflowSlug="planning")


def _dispatch_review(harness: FunctionalHarness, session_id: str) -> None:
    """Journal `draft` executed, then put the second step, `review`, in flight."""
    _dispatch_draft(harness, session_id)
    harness.invoke("check-step-postconditions", sessionId=session_id)
    harness.invoke("resolve-step", sessionId=session_id, workflowSlug="planning")


class TestResolveStepModel:
    """Function 4: the profile serving the in-flight step's dispatch, deterministically."""

    def test_the_in_flight_step_binds_the_highest_scoring_profile(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 4, Interface + invariant 3): the harness deduces the
        in-flight step from the session's own logs and answers the catalog profile
        whose weighted capability sum is highest — `draft` weights `coding: 8`, which
        scores `fast-coder` 72 against `deep-thinker` 40."""
        _dispatch_draft(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "resolved"}
        assert report["profile"]["slug"] == "fast-coder"
        assert report["profile"]["score"] == 72
        assert report["profile"]["costRank"] == 2
        assert report["context"]["workflowInstanceId"].startswith("planning-")

    def test_the_binding_follows_the_steps_own_declared_weights(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 4, invariants 1 and 3): the weights are the step's own
        static declaration, read from configuration at use time — `review` weights
        `deep-reasoning: 9`, which routes to `deep-thinker` at 81."""
        _dispatch_review(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["profile"]["slug"] == "deep-thinker"
        assert report["profile"]["score"] == 81

    def test_an_exact_tie_breaks_toward_cost_rank_then_slug(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 4, invariant 3): an exact tie breaks toward the lower
        `costRank`; if both are equal the lexicographically lowest slug wins."""
        harness = build_harness(model_profiles=TIED_MODEL_PROFILES)
        harness.invoke("start-session", sessionId="tie-session", agent="orchestrator")
        _dispatch_draft(harness, "tie-session")

        run = harness.invoke(FUNCTION, sessionId="tie-session")

        report = assert_contract_round_trip(harness, run)
        assert report["profile"] == {
            "slug": "alpha-twin",
            "score": 32,
            "costRank": 3,
            "reason": report["profile"]["reason"],
        }

    def test_the_binding_is_deterministic_and_journals_every_invocation(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 4, invariant 5 + Postconditions): any number of calls for
        the same step yields the identical profile, and one log entry records each
        (1 invocation = 1 entry)."""
        _dispatch_draft(harness, orchestrator_session)

        first = harness.invoke(FUNCTION, sessionId=orchestrator_session)
        second = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert assert_contract_round_trip(harness, second)["profile"] == (
            first.report["profile"]
        )
        assert harness.list_journaled_functions(orchestrator_session) == (
            "start-session",
            "resolve-step",
            FUNCTION,
            FUNCTION,
        )
        assert_journal_contract(harness, orchestrator_session)
        assert_report_journaled_byte_identically(harness, second, 3)

    def test_a_session_with_no_in_flight_step_is_not_applicable_and_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 4, precondition E + rule 2): with no in-flight step
        persisted state names no target — `not-applicable`, a success status carrying
        no payload and never journaled."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "not-applicable"}
        assert "profile" not in report
        assert harness.list_journaled_functions(orchestrator_session) == ("start-session",)

    def test_a_concluded_step_is_no_longer_a_target(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 2): after the first function-10 outcome the step is no longer in
        flight, so a re-delivered call finds no target."""
        _dispatch_draft(harness, orchestrator_session)
        harness.invoke("check-step-postconditions", sessionId=orchestrator_session)
        before = harness.list_journaled_functions(orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert assert_contract_round_trip(harness, run)["outcome"] == {
            "status": "not-applicable"
        }
        assert harness.list_journaled_functions(orchestrator_session) == before

    def test_an_unregistered_session_fails_closed_without_a_log(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 4, precondition E + rule 4): the mediated-invocation
        backstop rejects an id resolving to no registered session, and there is no log
        to journal that rejection to."""
        run = harness.invoke(FUNCTION, sessionId="ghost-session")

        assert_contract_round_trip(harness, run)
        assert run.status == "inquiry-error"
        assert run.error_code == "session-unregistered"
        assert not harness.is_session_logged("ghost-session")

    def test_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 3, C8): the refusal is `state-error` (`session-ended`) and no
        entry ever follows the ending entry."""
        _dispatch_draft(harness, orchestrator_session)
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
        run = harness.invoke(FUNCTION, sessionId="", parentSessionId="root")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert run.stderr.strip() != ""
