"""Functional tests for harness function 6, `resolve-step-instructions`.

Each test drives the assembled system through the real command entry point, validates
both sides of the round trip against the function's own contracts, and asserts the
journal the invocation left in the STEP session's log — the step's authored constraints
reaching the agent with no discretion of its own.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from functional_fixtures import (
    PLANNING_WORKFLOW,
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
    build_capabilities,
)

FUNCTION = "resolve-step-instructions"
STEP_SESSION = "draft-session"
REVIEW_SESSION = "review-session"

# The same workflow slug and step slug as the default catalog's `planning`, declaring
# OTHER instruction refs: the answer follows the declaration, never the session.
REDECLARED_PLANNING_WORKFLOW: Mapping[str, Any] = {
    "slug": "planning",
    "orchestrator": "orchestrator",
    "instructions": ["workflow-selection-handling"],
    "steps": [
        {
            "slug": "draft",
            "actor": "builder",
            "artifact": "report",
            "instructions": ["redeclared-guidance", "second-redeclared-guidance"],
            "capabilities": build_capabilities(coding=8),
        }
    ],
}


def _open_step_session(
    harness: FunctionalHarness,
    orchestrator_session: str,
    *,
    session_id: str = STEP_SESSION,
    agent: str = "builder",
    workflow_slug: str = "planning",
) -> str:
    """Resolve the workflow's next step, then open the step's own agent session."""
    harness.invoke(
        "resolve-step", sessionId=orchestrator_session, workflowSlug=workflow_slug
    )
    harness.invoke(
        "start-session",
        sessionId=session_id,
        parentSessionId=orchestrator_session,
        agent=agent,
    )
    return session_id


def _open_review_session(harness: FunctionalHarness, orchestrator_session: str) -> str:
    """Journal `draft` executed, then open the session of the second step, `review`."""
    _open_step_session(harness, orchestrator_session)
    harness.invoke("check-step-postconditions", sessionId=orchestrator_session)
    return _open_step_session(
        harness, orchestrator_session, session_id=REVIEW_SESSION, agent="reviewer"
    )


class TestResolveStepInstructions:
    """Function 6: the step's authored behavioral guidance, injected at session open."""

    def test_a_step_session_loads_exactly_its_steps_declared_instruction_refs(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 6, Postconditions + invariant 1): the session context contains
        exactly its step's declared refs — nothing more, nothing chosen by the agent —
        and the invocation appends its own entry to the step session's log."""
        step_session = _open_step_session(harness, orchestrator_session)

        run = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "resolved"}
        assert report["instructions"] == ["draft-guidance"]
        assert report["context"]["parentSessionId"] == orchestrator_session
        assert report["context"]["workflowInstanceId"].startswith("planning-")
        assert harness.list_journaled_functions(step_session) == (
            "start-session",
            FUNCTION,
        )
        assert assert_journal_contract(harness, step_session)[-1]["report"] == report
        assert_report_journaled_byte_identically(harness, run, 1)

    def test_the_refs_are_the_steps_own_not_the_workflows(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 6, invariant 1): instruction refs are declared PER STEP —
        the workflow-level refs belong to the orchestrator's session (function 1), never
        to a step session's."""
        step_session = _open_step_session(harness, orchestrator_session)

        run = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        resolved = assert_contract_round_trip(harness, run)["instructions"]
        assert resolved == list(PLANNING_WORKFLOW["steps"][0]["instructions"])
        assert set(resolved).isdisjoint(PLANNING_WORKFLOW["instructions"])

    def test_a_second_step_of_one_workflow_loads_its_own_refs(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 6, invariant 3): resolution is step-scoped — the correlated
        step decides, so the second step's session loads the second step's refs."""
        review_session = _open_review_session(harness, orchestrator_session)

        run = harness.invoke(
            FUNCTION, sessionId=review_session, parentSessionId=orchestrator_session
        )

        assert assert_contract_round_trip(harness, run)["instructions"] == [
            "review-guidance"
        ]

    def test_repeated_invocations_resolve_byte_identical_refs(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 6, invariant 3): resolution is deterministic — a pure
        configuration lookup, so a second invocation answers the same refs, and each
        invocation still journals its own entry (1 invocation = 1 entry)."""
        step_session = _open_step_session(harness, orchestrator_session)

        first = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )
        second = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        assert first.stdout == second.stdout
        assert harness.list_journaled_functions(step_session) == (
            "start-session",
            FUNCTION,
            FUNCTION,
        )

    def test_the_workflow_configuration_alone_decides_the_refs(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 6, invariant 3): the workflow configuration decides, never the
        agent — the same workflow, step, agent, and session ids answer other refs when
        the step declares other refs."""
        harness = build_harness(workflows={"planning": REDECLARED_PLANNING_WORKFLOW})
        harness.invoke("start-session", sessionId="root", agent="orchestrator")
        step_session = _open_step_session(harness, "root")

        run = harness.invoke(FUNCTION, sessionId=step_session, parentSessionId="root")

        assert assert_contract_round_trip(harness, run)["instructions"] == [
            "redeclared-guidance",
            "second-redeclared-guidance",
        ]

    def test_resolution_writes_nothing_beyond_its_own_entry(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 6, Postconditions): the invocation appends its own entry to the
        STEP session's log — the artifact plane and the dispatching session's log are
        untouched."""
        step_session = _open_step_session(harness, orchestrator_session)
        commits_before = harness.count_commits()
        parent_log_before = harness.read_log_lines(orchestrator_session)

        harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        assert harness.count_commits() == commits_before
        assert harness.list_committed_paths() == (".gitignore",)
        assert harness.read_log_lines(orchestrator_session) == parent_log_before

    def test_a_session_no_step_resolution_correlates_to_is_refused(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 6, precondition E): a session with no correlating unresolved
        `resolve-step` entry is the orchestrator's and loads functions 1-2 instead —
        `state-error` (`step-correlation-missing`), journaled."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "step-correlation-missing"
        assert "instructions" not in report
        assert harness.list_journaled_functions(orchestrator_session) == (
            "start-session",
            FUNCTION,
        )
        assert (
            assert_journal_contract(harness, orchestrator_session)[-1]["report"]
            == report
        )

    def test_a_concluded_correlation_is_refused(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 6, precondition E): the correlated resolution must have no
        later function-10 outcome — once the correlation concluded, the invocation is
        out of order: `state-error` (`step-correlation-missing`), journaled."""
        step_session = _open_step_session(harness, orchestrator_session)
        harness.invoke("check-step-postconditions", sessionId=orchestrator_session)

        run = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        assert assert_contract_round_trip(harness, run)["outcome"]["status"] == (
            "state-error"
        )
        assert run.error_code == "step-correlation-missing"
        assert harness.list_journaled_functions(step_session) == (
            "start-session",
            FUNCTION,
        )

    def test_an_unregistered_session_fails_closed_without_a_log(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): `session-unregistered` returns its report — the context is
        constructible — but has no log to journal to."""
        run = harness.invoke(FUNCTION, sessionId="ghost-session")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "inquiry-error"
        assert run.error_code == "session-unregistered"
        assert not harness.is_session_logged("ghost-session")

    def test_an_ended_session_is_refused_unjournaled(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 3, C8): a call against a session whose log carries an ending entry
        is `state-error` (`session-ended`) and is never journaled."""
        step_session = _open_step_session(harness, orchestrator_session)
        harness.invoke("end-session", sessionId=step_session)
        journaled_before = harness.read_log_lines(step_session)

        run = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        assert_contract_round_trip(harness, run)
        assert run.status == "state-error"
        assert run.error_code == "session-ended"
        assert harness.read_log_lines(step_session) == journaled_before

    def test_a_malformed_inquiry_produces_no_report_at_the_exit_plane(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): a contract-validation failure produces NO report at all — a
        contract-valid report cannot be built, so it surfaces at the command exit plane
        with stderr and a nonzero exit."""
        run = harness.invoke(
            FUNCTION, sessionId="step", parentSessionId="Orchestrator_Session"
        )

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert run.stderr.strip() != ""
