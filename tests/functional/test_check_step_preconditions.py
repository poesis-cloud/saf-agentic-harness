"""Functional tests for harness function 5, `check-step-preconditions`.

Each test drives the assembled system through the real command entry point, validates
both sides of the round trip against the function's own contracts, and asserts the
journal and the untouched artifact plane the invocation left behind.
"""

from __future__ import annotations

from typing import Callable

from functional_fixtures import (
    UNEVALUABLE_WORKFLOW,
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
)

FUNCTION = "check-step-preconditions"

AFTER_DRAFT_CONDITION = {"kind": "precondition", "slug": "after-draft", "step": "draft"}
REPORT_EXISTS_CONDITION = {
    "kind": "precondition",
    "slug": "report-exists",
    "setSelector": {"setQuery": "artifacts['report']"},
    "setPredicate": "size(selected) > 0",
}


def _dispatch_draft(harness: FunctionalHarness, session_id: str) -> None:
    """Put the planning workflow's first step, `draft`, in flight."""
    harness.invoke("resolve-step", sessionId=session_id, workflowSlug="planning")


def _dispatch_review(harness: FunctionalHarness, session_id: str) -> None:
    """Journal `draft` executed, then put the conditioned step, `review`, in flight."""
    _dispatch_draft(harness, session_id)
    harness.invoke("check-step-postconditions", sessionId=session_id)
    harness.invoke("resolve-step", sessionId=session_id, workflowSlug="planning")


class TestCheckStepPreconditions:
    """Function 5: the gate between resolution and dispatch, over persisted state."""

    def test_a_step_declaring_no_precondition_passes_vacuously(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 5, invariant 4): a step declaring zero preconditions passes
        vacuously — `pass` with an empty `conditionChecks`, an explicit journaled
        entry, never a skipped invocation."""
        _dispatch_draft(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "pass"}
        assert report["conditionChecks"] == []
        entries = assert_journal_contract(harness, orchestrator_session)
        assert entries[-1]["report"] == report
        assert_report_journaled_byte_identically(harness, run, 2)

    def test_a_missing_artifact_fails_the_gate_with_its_condition_logged_verbatim(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 5, invariants 1-3): the journaled predecessor passes, the
        unsatisfied `stateCondition` fails with a `failureMessage`, and every check
        logs the FULL condition object under its slug — the aggregate is `fail`."""
        _dispatch_review(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "fail"}
        checks = report["conditionChecks"]
        assert [check["condition"] for check in checks] == [
            AFTER_DRAFT_CONDITION,
            REPORT_EXISTS_CONDITION,
        ]
        assert checks[0]["outcome"] == "pass"
        assert checks[1]["outcome"] == "fail"
        assert "report-exists" in checks[1]["failureMessage"]
        assert assert_journal_contract(harness, orchestrator_session)[-1]["report"] == report

    def test_committed_state_satisfying_every_condition_passes_the_gate(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 5, invariant 2 + C1): conditions are evaluated against
        persisted workspace state — a committed artifact makes the selected set
        non-empty and the predicate true."""
        harness.commit_artifact("report/alpha.json", {"slug": "alpha", "state": "drafted"})
        _dispatch_review(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "pass"}
        assert [check["outcome"] for check in report["conditionChecks"]] == ["pass", "pass"]

    def test_an_uncommitted_artifact_does_not_satisfy_a_condition(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (Workspace Git plane 1 + C0): committed state IS workspace state — the
        working tree is only the write staging area, so an uncommitted file satisfies
        nothing."""
        staged = harness.workspace_dir / "report" / "beta.json"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_text('{"slug": "beta", "state": "drafted"}', encoding="utf-8")
        _dispatch_review(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert assert_contract_round_trip(harness, run)["outcome"] == {"status": "fail"}

    def test_checking_never_touches_the_artifact_plane(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 5, Postconditions): no artifact is touched — the
        invocation's own log entry is the only write."""
        harness.commit_artifact("report/alpha.json", {"slug": "alpha", "state": "drafted"})
        _dispatch_review(harness, orchestrator_session)
        commits_before = harness.count_commits()

        harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert harness.count_commits() == commits_before
        assert harness.list_committed_paths() == (".gitignore", "report/alpha.json")

    def test_a_session_with_no_in_flight_step_is_not_applicable_and_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 5, precondition E + rule 2): with no resolved step in hand
        persisted state names no target — `not-applicable`, never journaled."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "not-applicable"}
        assert "conditionChecks" not in report
        assert harness.list_journaled_functions(orchestrator_session) == ("start-session",)

    def test_a_duplicate_step_ended_delivery_is_absorbed(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 2): after the first function-10 outcome the step is no longer in
        flight, so a re-delivered boundary finds no target and journals nothing."""
        _dispatch_draft(harness, orchestrator_session)
        harness.invoke("check-step-postconditions", sessionId=orchestrator_session)
        before = harness.list_journaled_functions(orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert assert_contract_round_trip(harness, run)["outcome"] == {
            "status": "not-applicable"
        }
        assert harness.list_journaled_functions(orchestrator_session) == before

    def test_an_expression_failing_at_runtime_is_a_journaled_state_error(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 5, invariant 2): a CEL expression failing AT RUNTIME — here
        a predicate that yields no boolean — is `state-error`
        (`condition-evaluation-failed`), journaled, the detail naming the slug."""
        harness = build_harness(workflows={"probing": UNEVALUABLE_WORKFLOW})
        harness.invoke("start-session", sessionId="probe-session", agent="orchestrator")
        harness.invoke("resolve-step", sessionId="probe-session", workflowSlug="probing")

        run = harness.invoke(FUNCTION, sessionId="probe-session")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "condition-evaluation-failed"
        assert "unevaluable" in report["outcome"]["error"]["message"]
        assert assert_journal_contract(harness, "probe-session")[-1]["report"] == report

    def test_an_unregistered_session_fails_closed_without_a_log(
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
        run = harness.invoke(FUNCTION, sessionId="root", parentSessionId="Parent_Session")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert run.stderr.strip() != ""
