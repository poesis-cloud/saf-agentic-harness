"""Functional tests for harness function 7, `resolve-step-skills`.

The correlation and determinism of function 6, for skills: a session's capabilities are
step-scoped by construction. Each test drives the real command entry point, validates
the round trip against the function's own contracts, and asserts the journal left in the
step session's log — alongside function 6's entry.
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

FUNCTION = "resolve-step-skills"
INSTRUCTIONS_FUNCTION = "resolve-step-instructions"
STEP_SESSION = "draft-session"
REVIEW_SESSION = "review-session"

# The default catalog's `planning` workflow with OTHER skills declared on its first
# step: the step declaration decides, nothing about the session does.
REDECLARED_PLANNING_WORKFLOW: Mapping[str, Any] = {
    "slug": "planning",
    "orchestrator": "orchestrator",
    "skills": ["workflow-selection"],
    "instructions": ["workflow-selection-handling"],
    "steps": [
        {
            "slug": "draft",
            "actor": "builder",
            "artifact": "report",
            "skills": ["redeclared-skill", "second-redeclared-skill"],
            "instructions": ["draft-guidance"],
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
) -> str:
    """Resolve the planning workflow's next step, then open the step's own session."""
    harness.invoke(
        "resolve-step", sessionId=orchestrator_session, workflowSlug="planning"
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


class TestResolveStepSkills:
    """Function 7: the agent's toolbox is its step's toolbox, by construction."""

    def test_a_step_session_loads_exactly_its_steps_declared_skills(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 7, Postconditions + invariant 1): the session loads exactly its
        step's declared skills, and the invocation appends its own entry to the step
        session's log."""
        step_session = _open_step_session(harness, orchestrator_session)

        run = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "resolved"}
        assert report["skills"] == ["drafting"]
        assert report["context"]["workflowInstanceId"].startswith("planning-")
        assert harness.list_journaled_functions(step_session) == (
            "start-session",
            FUNCTION,
        )
        assert assert_journal_contract(harness, step_session)[-1]["report"] == report
        assert_report_journaled_byte_identically(harness, run, 1)

    def test_the_skills_are_per_step_never_the_workflows(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 7, invariant 1): skill ids are declared per step, NOT per
        workflow — the workflow-level skills stay with the orchestrator (function 2)."""
        step_session = _open_step_session(harness, orchestrator_session)

        run = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        resolved = assert_contract_round_trip(harness, run)["skills"]
        assert resolved == list(PLANNING_WORKFLOW["steps"][0]["skills"])
        assert set(resolved).isdisjoint(PLANNING_WORKFLOW["skills"])

    def test_a_step_declaring_no_skills_loads_an_empty_toolbox(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 7, invariant 1): a session loads exactly its step's skills —
        a step declaring none loads none, rather than inheriting the workflow's."""
        review_session = _open_review_session(harness, orchestrator_session)
        assert "skills" not in PLANNING_WORKFLOW["steps"][1]

        run = harness.invoke(
            FUNCTION, sessionId=review_session, parentSessionId=orchestrator_session
        )

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "resolved"}
        assert report["skills"] == []

    def test_functions_6_and_7_journal_side_by_side_in_the_step_sessions_log(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 7, Postconditions): the invocation appends its own entry to the
        step session's log, ALONGSIDE function 6's — one entry per invocation."""
        step_session = _open_step_session(harness, orchestrator_session)

        harness.invoke(
            INSTRUCTIONS_FUNCTION,
            sessionId=step_session,
            parentSessionId=orchestrator_session,
        )
        harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        assert harness.list_journaled_functions(step_session) == (
            "start-session",
            INSTRUCTIONS_FUNCTION,
            FUNCTION,
        )
        entries = assert_journal_contract(harness, step_session)
        assert entries[1]["report"]["instructions"] == ["draft-guidance"]
        assert entries[2]["report"]["skills"] == ["drafting"]

    def test_repeated_invocations_resolve_byte_identical_skills(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 7, invariant 3): resolution is deterministic — a second
        invocation over unchanged configuration answers the same skills."""
        step_session = _open_step_session(harness, orchestrator_session)

        first = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )
        second = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        assert first.stdout == second.stdout

    def test_the_step_declaration_alone_decides_the_skills(
        self, build_harness: Callable[..., FunctionalHarness]
    ) -> None:
        """Spec (function 7, invariant 3): the step declaration decides — same workflow,
        same step, same agent, other declaration, other skills."""
        harness = build_harness(workflows={"planning": REDECLARED_PLANNING_WORKFLOW})
        harness.invoke("start-session", sessionId="root", agent="orchestrator")
        step_session = _open_step_session(harness, "root")

        run = harness.invoke(FUNCTION, sessionId=step_session, parentSessionId="root")

        assert assert_contract_round_trip(harness, run)["skills"] == [
            "redeclared-skill",
            "second-redeclared-skill",
        ]

    def test_resolution_writes_nothing_beyond_its_own_entry(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 7, Postconditions): the step session's own entry is the only
        write — the artifact plane and the dispatching session's log are untouched."""
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
        """Spec (function 7, Preconditions): function 6's preconditions and violation
        outcomes apply identically — no correlating unresolved `resolve-step` entry is
        `state-error` (`step-correlation-missing`), journaled."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "state-error"
        assert run.error_code == "step-correlation-missing"
        assert "skills" not in report
        assert (
            assert_journal_contract(harness, orchestrator_session)[-1]["report"]
            == report
        )

    def test_a_concluded_correlation_is_refused(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 7, invariant 2): correlation is identical to function 6's —
        once the step's function-10 outcome journaled, the resolution is resolved and
        correlates to nothing."""
        step_session = _open_step_session(harness, orchestrator_session)
        harness.invoke("check-step-postconditions", sessionId=orchestrator_session)

        run = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        assert_contract_round_trip(harness, run)
        assert run.error_code == "step-correlation-missing"
        assert harness.list_journaled_functions(step_session) == (
            "start-session",
            FUNCTION,
        )

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
        """Spec (rule 3, C8): the refusal is `state-error` (`session-ended`) and no entry
        ever follows the ending entry."""
        step_session = _open_step_session(harness, orchestrator_session)
        harness.invoke("end-session", sessionId=step_session)
        journaled_before = harness.read_log_lines(step_session)

        run = harness.invoke(
            FUNCTION, sessionId=step_session, parentSessionId=orchestrator_session
        )

        assert_contract_round_trip(harness, run)
        assert run.error_code == "session-ended"
        assert harness.read_log_lines(step_session) == journaled_before

    def test_a_malformed_inquiry_produces_no_report_at_the_exit_plane(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): a contract-validation failure produces no report at all and
        surfaces at the command exit plane."""
        run = harness.invoke(FUNCTION, sessionId="Step-Session")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert run.stderr.strip() != ""
