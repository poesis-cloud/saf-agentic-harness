"""Functional tests for harness function 10, `check-step-postconditions`.

The producer of the step outcome that drives the whole instance: function 3's cursor
reads nothing else. Each test drives the real command entry point, validates the round
trip against the function's own contracts, and asserts the dispatching session's journal
and the untouched artifact plane the invocation left behind.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from functional_fixtures import (
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
    build_capabilities,
    run_git,
)

FUNCTION = "check-step-postconditions"

REPORT_DELIVERED_CONDITION: Mapping[str, Any] = {
    "kind": "postcondition",
    "slug": "report-delivered",
    "setSelector": {"setQuery": "artifacts['report']"},
    "setPredicate": "size(selected) > 0",
}
UNBLOCKS_VERIFY_CONDITION: Mapping[str, Any] = {
    "kind": "postcondition",
    "slug": "unblocks-verify",
    "step": "verify",
}

# Two steps: the first declares both kinds of postcondition, the second gates on it —
# so the journaled outcome of the first IS what function 3's cursor reads next.
DELIVERY_WORKFLOW: Mapping[str, Any] = {
    "slug": "delivery",
    "orchestrator": "orchestrator",
    "skills": ["workflow-selection"],
    "instructions": ["workflow-selection-handling"],
    "steps": [
        {
            "slug": "produce",
            "actor": "builder",
            "artifact": "report",
            "instructions": ["draft-guidance"],
            "capabilities": build_capabilities(coding=8),
            "conditions": [UNBLOCKS_VERIFY_CONDITION, REPORT_DELIVERED_CONDITION],
        },
        {
            "slug": "verify",
            "actor": "reviewer",
            "artifact": "report",
            "instructions": ["review-guidance"],
            "capabilities": build_capabilities(deep_reasoning=9),
            "conditions": [
                {"kind": "precondition", "slug": "after-produce", "step": "produce"}
            ],
        },
    ],
}

# A postcondition whose predicate yields a list rather than a boolean: the expression
# fails at RUNTIME, which function 10 owes a `state-error`.
UNEVALUABLE_DELIVERY_WORKFLOW: Mapping[str, Any] = {
    "slug": "delivery",
    "orchestrator": "orchestrator",
    "instructions": ["workflow-selection-handling"],
    "steps": [
        {
            "slug": "produce",
            "actor": "builder",
            "artifact": "report",
            "instructions": ["draft-guidance"],
            "capabilities": build_capabilities(coding=8),
            "conditions": [
                {
                    "kind": "postcondition",
                    "slug": "unevaluable",
                    "setSelector": {"setQuery": "artifacts['report']"},
                    "setPredicate": "selected",
                }
            ],
        }
    ],
}


def _open_delivery_session(
    build_harness: Callable[..., FunctionalHarness],
    workflows: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[FunctionalHarness, str]:
    """Build a delivery rig, register its orchestrator, and put `produce` in flight."""
    harness = build_harness(
        workflows={"delivery": DELIVERY_WORKFLOW} if workflows is None else workflows
    )
    harness.invoke("start-session", sessionId="root", agent="orchestrator")
    harness.invoke("resolve-step", sessionId="root", workflowSlug="delivery")
    return harness, "root"


def _resolved_step(harness: FunctionalHarness, session_id: str) -> str | None:
    """Answer the step slug the session's latest `resolve-step` entry resolved."""
    for entry in reversed(harness.read_log(session_id)):
        if entry["report"]["context"]["function"] == "resolve-step":
            step = entry["report"].get("step")
            return None if step is None else step["slug"]
    return None


class TestCheckStepPostconditions:
    """Function 10: did this step deliver? — evaluated once, at the step-ended boundary."""

    def test_a_delivered_step_passes_with_one_check_per_declared_postcondition(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, Out + Postconditions): the aggregate outcome plus one check
        per declared postcondition, logging the FULL condition object, appended to the
        DISPATCHING session's log."""
        harness, session = _open_delivery_session(build_harness)
        harness.commit_artifact("report/alpha.json", {"slug": "alpha", "state": "done"})

        run = harness.invoke(FUNCTION, sessionId=session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "pass"}
        checks = report["conditionChecks"]
        assert [check["condition"] for check in checks] == [
            UNBLOCKS_VERIFY_CONDITION,
            REPORT_DELIVERED_CONDITION,
        ]
        assert [check["outcome"] for check in checks] == ["pass", "pass"]
        assert harness.list_journaled_functions(session) == (
            "start-session",
            "resolve-step",
            FUNCTION,
        )
        assert assert_journal_contract(harness, session)[-1]["report"] == report
        assert_report_journaled_byte_identically(harness, run, 2)

    def test_an_undelivered_step_fails_with_its_failure_message(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, invariant 1 + C2): `state` assertions evaluate over
        persisted artifacts only — with nothing committed the state postcondition fails,
        carrying its advisory failure message, and the aggregate is `fail`."""
        harness, session = _open_delivery_session(build_harness)

        run = harness.invoke(FUNCTION, sessionId=session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "fail"}
        checks = report["conditionChecks"]
        assert [check["outcome"] for check in checks] == ["pass", "fail"]
        assert "report-delivered" in checks[1]["failureMessage"]
        assert assert_journal_contract(harness, session)[-1]["report"] == report

    def test_a_step_declaring_zero_postconditions_passes_vacuously(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 10, invariant 4): a step declaring zero postconditions passes
        vacuously — `pass` with an empty `conditionChecks`, journaled, and sufficient for
        the cursor to count the step executed."""
        harness.invoke(
            "resolve-step", sessionId=orchestrator_session, workflowSlug="planning"
        )

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "pass"}
        assert report["conditionChecks"] == []
        assert (
            assert_journal_contract(harness, orchestrator_session)[-1]["report"]
            == report
        )

    def test_a_passing_outcome_advances_the_cursor_to_the_next_step(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, invariant 3): the journaled outcome is exactly what
        function 3's cursor reads — a step whose latest outcome passes counts executed,
        so the next resolution answers the successor, in the same instance."""
        harness, session = _open_delivery_session(build_harness)
        harness.commit_artifact("report/alpha.json", {"slug": "alpha", "state": "done"})
        instance = harness.read_log(session)[-1]["report"]["context"][
            "workflowInstanceId"
        ]

        harness.invoke(FUNCTION, sessionId=session)
        run = harness.invoke("resolve-step", sessionId=session, workflowSlug="delivery")

        report = assert_contract_round_trip(harness, run)
        assert report["step"]["slug"] == "verify"
        assert report["context"]["workflowInstanceId"] == instance

    def test_a_failing_outcome_leaves_the_step_unexecuted_for_the_cursor(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, invariant 3 + Caller usage): the failed step is not
        journaled executed, so the cursor resolves THE SAME step again — the failure
        stays inside the workflow instance."""
        harness, session = _open_delivery_session(build_harness)
        instance = harness.read_log(session)[-1]["report"]["context"][
            "workflowInstanceId"
        ]

        harness.invoke(FUNCTION, sessionId=session)
        run = harness.invoke("resolve-step", sessionId=session, workflowSlug="delivery")

        report = assert_contract_round_trip(harness, run)
        assert report["step"]["slug"] == "produce"
        assert report["context"]["workflowInstanceId"] == instance

    def test_a_session_with_no_in_flight_step_is_not_applicable_and_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 10, precondition E + rule 2): with no in-flight step persisted
        state names no target — `not-applicable`, a success status carrying no payload
        and NEVER journaled."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "not-applicable"}
        assert "conditionChecks" not in report
        assert harness.list_journaled_functions(orchestrator_session) == (
            "start-session",
        )

    def test_a_duplicate_step_ended_delivery_is_absorbed(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, invariant 2 + precondition E): postconditions are evaluated
        ONCE per step pass — after the first outcome journals the step is no longer in
        flight, so a re-delivered boundary finds no target and adds no second
        evaluation."""
        harness, session = _open_delivery_session(build_harness)
        harness.commit_artifact("report/alpha.json", {"slug": "alpha", "state": "done"})
        harness.invoke(FUNCTION, sessionId=session)
        journaled_before = harness.read_log_lines(session)

        run = harness.invoke(FUNCTION, sessionId=session)

        assert assert_contract_round_trip(harness, run)["outcome"] == {
            "status": "not-applicable"
        }
        assert harness.read_log_lines(session) == journaled_before

    def test_the_evaluation_touches_no_artifact(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, Postconditions): no artifact is touched — the invocation's
        own log entry is the only write, leaving the workspace tree clean."""
        harness, session = _open_delivery_session(build_harness)
        harness.commit_artifact("report/alpha.json", {"slug": "alpha", "state": "done"})
        commits_before = harness.count_commits()

        harness.invoke(FUNCTION, sessionId=session)

        assert harness.count_commits() == commits_before
        assert harness.list_committed_paths() == (".gitignore", "report/alpha.json")
        assert run_git(harness.workspace_dir, "status", "--porcelain") == ""

    def test_an_expression_failing_at_runtime_is_a_journaled_state_error(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, invariant 1): a CEL expression failing at runtime is
        `state-error` (`condition-evaluation-failed`), journaled, exactly as function 5,
        invariant 2."""
        harness, session = _open_delivery_session(
            build_harness, {"delivery": UNEVALUABLE_DELIVERY_WORKFLOW}
        )

        run = harness.invoke(FUNCTION, sessionId=session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "condition-evaluation-failed"
        assert "unevaluable" in report["outcome"]["error"]["message"]
        assert assert_journal_contract(harness, session)[-1]["report"] == report

    def test_a_journaled_error_outcome_never_counts_the_step_executed(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, invariant 3): a step counts executed only where its LATEST
        outcome passes — a journaled `state-error` is an ordinary outcome (rule 1) that
        concludes the evaluation without passing, so the cursor resolves the step
        again."""
        harness, session = _open_delivery_session(
            build_harness, {"delivery": UNEVALUABLE_DELIVERY_WORKFLOW}
        )
        instance = harness.read_log(session)[-1]["report"]["context"][
            "workflowInstanceId"
        ]
        harness.invoke(FUNCTION, sessionId=session)

        run = harness.invoke("resolve-step", sessionId=session, workflowSlug="delivery")

        report = assert_contract_round_trip(harness, run)
        assert report["step"]["slug"] == "produce"
        assert report["context"]["workflowInstanceId"] == instance

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
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (rule 3, C8): a call against an ended session is `state-error`
        (`session-ended`) and is the one `state-error` that never journals."""
        harness, session = _open_delivery_session(build_harness)
        harness.invoke("end-session", sessionId=session)
        journaled_before = harness.read_log_lines(session)

        run = harness.invoke(FUNCTION, sessionId=session)

        assert_contract_round_trip(harness, run)
        assert run.status == "state-error"
        assert run.error_code == "session-ended"
        assert harness.read_log_lines(session) == journaled_before

    def test_a_malformed_inquiry_produces_no_report_at_the_exit_plane(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): a contract-validation failure produces no report at all and
        surfaces at the command exit plane with stderr and a nonzero exit."""
        run = harness.invoke(FUNCTION, sessionId="root/../escape")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert run.stderr.strip() != ""

    def test_the_dispatching_session_owns_the_step_outcome(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 10, Trigger + Postconditions): the invocation runs in the
        dispatching (orchestrator) session — at step-ended the step's own session has
        already closed, and its ending adds no second evaluation."""
        harness, session = _open_delivery_session(build_harness)
        harness.invoke(
            "start-session",
            sessionId="produce-session",
            parentSessionId=session,
            agent="builder",
        )
        harness.invoke("end-session", sessionId="produce-session")

        harness.invoke(FUNCTION, sessionId=session)

        assert harness.list_journaled_functions(session) == (
            "start-session",
            "resolve-step",
            FUNCTION,
        )
        assert harness.list_journaled_functions("produce-session") == (
            "start-session",
            "end-session",
        )
        assert _resolved_step(harness, session) == "produce"
