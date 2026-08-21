"""Functional tests for harness function 11, `end-session`.

The session's counterpart to function 0: it closes a session's log with a final entry,
idempotently, and is the ONE function an ended session does not refuse (C8). Each test
drives the real command entry point and asserts the log the invocation closed — and
never grew again.
"""

from __future__ import annotations

from functional_fixtures import (
    FunctionalHarness,
    assert_contract_round_trip,
    assert_journal_contract,
    assert_report_journaled_byte_identically,
)

FUNCTION = "end-session"

# Every other session-bound function of the twelve, as a host would call it against the
# session it just ended — each owes the C8 refusal (rule 3).
C8_REFUSED_INVOCATIONS: tuple[tuple[str, dict[str, str]], ...] = (
    ("start-session", {"agent": "orchestrator"}),
    ("resolve-workflow-instructions", {}),
    ("resolve-workflow-skills", {}),
    ("resolve-step", {"workflowSlug": "planning"}),
    ("resolve-step-model", {}),
    ("check-step-preconditions", {}),
    ("resolve-step-instructions", {}),
    ("resolve-step-skills", {}),
    ("check-step-postconditions", {}),
)


def _open_step_session(harness: FunctionalHarness, orchestrator_session: str) -> str:
    """Put the planning workflow's first step in flight, then open its agent session."""
    harness.invoke(
        "resolve-step", sessionId=orchestrator_session, workflowSlug="planning"
    )
    harness.invoke(
        "start-session",
        sessionId="draft-session",
        parentSessionId=orchestrator_session,
        agent="builder",
    )
    return "draft-session"


class TestEndSession:
    """Function 11: where starting precedes everything, ending follows everything."""

    def test_ending_an_open_session_appends_the_ending_entry_as_its_last_line(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 11, Postconditions): the session's log carries an ending entry
        as its last line when the session was open."""
        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "ended"}
        assert report["context"]["workflowInstanceId"] is None
        assert harness.list_journaled_functions(orchestrator_session) == (
            "start-session",
            FUNCTION,
        )
        assert assert_journal_contract(harness, orchestrator_session)[-1]["report"] == (
            report
        )
        assert_report_journaled_byte_identically(harness, run, 1)

    def test_the_parent_chain_comes_from_the_sessions_own_start_entry(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 11, In): `sessionId` and nothing else — `parentSessionId` is
        already on record from this session's own start entry, and this function's only
        job is to close the log that entry started."""
        step_session = _open_step_session(harness, orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=step_session)

        report = assert_contract_round_trip(harness, run)
        assert "parentSessionId" not in run.inquiry
        assert report["context"]["parentSessionId"] == orchestrator_session

    def test_a_re_delivered_ending_appends_no_second_entry(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 11, invariant 2): re-delivery of the same session-end signal
        appends no second ending entry and is never an error — the call returns the same
        `ended` outcome (idempotent no-op)."""
        first = harness.invoke(FUNCTION, sessionId=orchestrator_session)
        journaled_after_first = harness.read_log_lines(orchestrator_session)

        second = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert assert_contract_round_trip(harness, second)["outcome"] == {
            "status": "ended"
        }
        assert second.stdout == first.stdout
        assert harness.read_log_lines(orchestrator_session) == journaled_after_first

    def test_ending_a_never_started_session_is_a_no_op(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (function 11, invariant 2): an ending call against a session never
        started is never an error — the same `ended` outcome, and no log is created."""
        run = harness.invoke(FUNCTION, sessionId="ghost-session")

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "ended"}
        assert report["context"]["parentSessionId"] is None
        assert not harness.is_session_logged("ghost-session")

    def test_no_entry_ever_follows_the_ending_entry(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 11, invariant 1 + rule 3): ending is the mirror of starting —
        every other session-bound function refuses an ended session with `state-error`
        (`session-ended`), unjournaled, so the log never grows again."""
        harness.invoke(
            "resolve-step", sessionId=orchestrator_session, workflowSlug="planning"
        )
        harness.invoke(FUNCTION, sessionId=orchestrator_session)
        closed_log = harness.read_log_lines(orchestrator_session)

        for function, arguments in C8_REFUSED_INVOCATIONS:
            run = harness.invoke(function, sessionId=orchestrator_session, **arguments)
            report = assert_contract_round_trip(harness, run)
            assert report["outcome"]["status"] == "state-error", function
            assert run.error_code == "session-ended", function

        assert harness.read_log_lines(orchestrator_session) == closed_log

    def test_function_11_alone_is_exempt_from_the_c8_refusal(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (rule 1 + C8): the C8 refusal applies to functions 0-10; function 11 is
        exempt — it answers `ended`, never `state-error`, against an ended session."""
        harness.invoke(FUNCTION, sessionId=orchestrator_session)

        run = harness.invoke(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"]["status"] == "ended"
        assert "error" not in report["outcome"]

    def test_ending_asserts_nothing_about_the_workflow_instance(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (function 11, invariant 4): a workflow instance's openness is computed
        structurally from its own steps' journaled outcomes, entirely independent of
        whether the session that resolved it has since ended."""
        harness.invoke(
            "resolve-step", sessionId=orchestrator_session, workflowSlug="planning"
        )
        instance = harness.read_log(orchestrator_session)[-1]["report"]["context"][
            "workflowInstanceId"
        ]
        harness.invoke("check-step-postconditions", sessionId=orchestrator_session)
        harness.invoke(FUNCTION, sessionId=orchestrator_session)

        harness.invoke("start-session", sessionId="successor", agent="orchestrator")
        run = harness.invoke(
            "resolve-step", sessionId="successor", workflowSlug="planning"
        )

        report = assert_contract_round_trip(harness, run)
        assert report["context"]["workflowInstanceId"] == instance
        assert report["step"]["slug"] == "review"

    def test_ending_touches_no_artifact(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (C1, Logging): the ending entry is the invocation's only write — logs are
        local-only and never committed, so the artifact plane is untouched."""
        commits_before = harness.count_commits()

        harness.invoke(FUNCTION, sessionId=orchestrator_session)

        assert harness.count_commits() == commits_before
        assert harness.list_committed_paths() == (".gitignore",)

    def test_the_entry_shim_ends_a_session_in_a_real_process(
        self, harness: FunctionalHarness, orchestrator_session: str
    ) -> None:
        """Spec (Functional testing): the real command entry point — `harness.py` over
        the framework the environment anchors — closes the log of a real process's
        invocation, exactly as the in-process composition root does."""
        run = harness.invoke_entry_shim(FUNCTION, sessionId=orchestrator_session)

        report = assert_contract_round_trip(harness, run)
        assert report["outcome"] == {"status": "ended"}
        assert harness.list_journaled_functions(orchestrator_session) == (
            "start-session",
            FUNCTION,
        )

    def test_a_malformed_inquiry_produces_no_report_at_the_exit_plane(
        self, harness: FunctionalHarness
    ) -> None:
        """Spec (rule 4): a `sessionId` that becomes a log filename must be a safe slug —
        a contract-validation failure produces no report at all and surfaces at the
        command exit plane."""
        run = harness.invoke(FUNCTION, sessionId="../escape")

        assert harness.validate_inquiry(FUNCTION, run.inquiry) != ()
        assert run.report is None
        assert run.exit_code != 0
        assert run.stderr.strip() != ""
