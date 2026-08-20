"""Unit tests for the step instruction resolver — harness function 6."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import WorkflowCatalog
from errors import InquiryError
from services.context_resolution import StepInstructionResolver
from stores.session_log_store import Log, SessionLogStore

_INSTANCE_ID = "verification-01J9XQ"


@pytest.fixture
def resolver(
    workflow_catalog: WorkflowCatalog, session_log_store: SessionLogStore
) -> StepInstructionResolver:
    """Provide the service under test with its injected collaborators."""
    return StepInstructionResolver(
        workflow_catalog=workflow_catalog,
        session_log_store=session_log_store,
    )


class TestStepInstructionResolver:
    """Cover harness function 6 (resolve-step-instructions)."""

    def test_resolve_step_instructions_returns_the_correlated_step_refs(
        self, resolver: StepInstructionResolver, step_session: str
    ) -> None:
        """Spec function 6, invariant 1 + worked example: instruction refs are declared per
        step in the workflow configuration, and the report carries the correlated step
        resolution's workflow instance."""
        report = resolver.resolve_step_instructions(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert report.outcome.status == "resolved"
        assert report.outcome.error is None
        assert report.context.function == "resolve-step-instructions"
        assert report.context.parent_session_id == "sess-orch"
        assert report.context.workflow_instance_id == _INSTANCE_ID
        assert report.instructions == ("review-handoff",)

    def test_resolve_step_instructions_journals_its_own_entry(
        self, resolver: StepInstructionResolver, step_session: str, read_journal
    ) -> None:
        """Spec function 6, Postconditions: the invocation appends its own entry to the
        step session's log — what was resolved, for which step."""
        resolver.resolve_step_instructions(
            session_id=step_session, parent_session_id="sess-orch"
        )

        entries = read_journal(step_session)
        assert len(entries) == 2
        assert entries[1]["report"]["context"]["function"] == "resolve-step-instructions"
        assert entries[1]["report"]["context"]["workflowInstanceId"] == _INSTANCE_ID
        assert entries[1]["report"]["instructions"] == ["review-handoff"]

    def test_resolve_step_instructions_lets_configuration_alone_decide(
        self, resolver: StepInstructionResolver, step_session: str, read_journal
    ) -> None:
        """Spec function 6, invariant 3: resolution is deterministic and step-scoped — the
        workflow configuration decides, never the agent and never the journaled payload."""
        journaled_step = read_journal("sess-orch")[1]["report"]["step"]

        report = resolver.resolve_step_instructions(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert journaled_step["instructions"] == "stale-instruction"
        assert report.instructions == ("review-handoff",)

    def test_resolve_step_instructions_correlates_on_the_session_agent(
        self,
        resolver: StepInstructionResolver,
        register_session,
        journal_resolution,
        journal_postconditions,
    ) -> None:
        """Spec function 6, invariant 2: the correlation is the parent session's latest
        `step-resolution` entry whose actor is the session's agent and whose step has no
        later function-10 outcome."""
        register_session("sess-orch", "orchestrator")
        journal_resolution("sess-orch", actor="developer", step_slug="pair")
        journal_postconditions("sess-orch")
        journal_resolution(
            "sess-orch", actor="reviewer", step_slug="review", timestamp="2026-08-18T09:03:00Z"
        )
        register_session("sess-step", "reviewer", parent_session_id="sess-orch")

        report = resolver.resolve_step_instructions(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "resolved"
        assert report.instructions == ("review-handoff",)

    def test_resolve_step_instructions_refuses_a_session_with_no_correlation(
        self,
        resolver: StepInstructionResolver,
        register_session,
        journal_resolution,
        read_journal,
    ) -> None:
        """Spec function 6, precondition (E): a session with no unresolved `resolve-step`
        entry correlating to it is `state-error` (`step-correlation-missing`), journaled."""
        register_session("sess-orch", "orchestrator")
        journal_resolution("sess-orch", actor="developer", step_slug="pair")
        register_session("sess-step", "reviewer", parent_session_id="sess-orch")

        report = resolver.resolve_step_instructions(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "step-correlation-missing"
        assert report.outcome.error.message
        assert report.instructions is None
        entries = read_journal("sess-step")
        assert len(entries) == 2
        assert "instructions" not in entries[1]["report"]

    def test_resolve_step_instructions_refuses_a_concluded_correlation(
        self,
        resolver: StepInstructionResolver,
        step_session: str,
        journal_postconditions,
        read_journal,
    ) -> None:
        """Spec function 6, precondition (E): the correlation concluded — the resolution
        carries a later function-10 outcome — so the invocation is out of order:
        `state-error` (`step-correlation-missing`), journaled."""
        journal_postconditions("sess-orch")

        report = resolver.resolve_step_instructions(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "step-correlation-missing"
        assert len(read_journal(step_session)) == 2

    def test_resolve_step_instructions_refuses_an_unregistered_session(
        self, resolver: StepInstructionResolver, workspace: Path
    ) -> None:
        """Spec function 6, precondition (E) + Outcomes rule 4: an unregistered session is
        `inquiry-error` (`session-unregistered`), with no log to journal to."""
        report = resolver.resolve_step_instructions(
            session_id="sess-unknown", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error.code == "session-unregistered"
        assert report.instructions is None
        assert not (workspace / "logs" / "sess-unknown.log.jsonl").exists()

    def test_resolve_step_instructions_reports_a_configuration_error_for_an_absent_step(
        self,
        resolver: StepInstructionResolver,
        register_session,
        journal_resolution,
        read_journal,
    ) -> None:
        """Spec Outcomes rule 1, `configuration-error`: configuration invalid at use time —
        a correlated step the loaded workflow no longer declares, journaled."""
        register_session("sess-orch", "orchestrator")
        journal_resolution("sess-orch", actor="reviewer", step_slug="ghost")
        register_session("sess-step", "reviewer", parent_session_id="sess-orch")

        report = resolver.resolve_step_instructions(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "configuration-error"
        assert report.outcome.error.message
        assert report.instructions is None
        assert len(read_journal("sess-step")) == 2

    def test_resolve_step_instructions_rejects_a_non_slug_session_id(
        self, resolver: StepInstructionResolver, workspace: Path
    ) -> None:
        """Spec Outcomes rule 1 + rule 4: a non-slug `sessionId` is `inquiry-error`
        (`invalid-inquiry`) — unjournalable."""
        with pytest.raises(InquiryError) as raised:
            resolver.resolve_step_instructions(
                session_id="../escape", parent_session_id="sess-orch"
            )

        assert raised.value.code == "invalid-inquiry"
        assert not (workspace / "logs").exists()

    def test_resolve_step_instructions_refuses_an_ended_session(
        self,
        resolver: StepInstructionResolver,
        step_session: str,
        end_session_log,
        read_journal,
    ) -> None:
        """Spec C8 + Outcomes rule 3: a call against an ended session is `state-error`
        (`session-ended`), never journaled."""
        end_session_log(step_session)

        report = resolver.resolve_step_instructions(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-ended"
        assert len(read_journal(step_session)) == 2

    def test_resolve_step_instructions_reports_a_system_error_on_append_failure(
        self,
        workflow_catalog: WorkflowCatalog,
        failing_session_log_store,
        registration_entry,
        resolution_entry,
    ) -> None:
        """Spec Outcomes rule 1, `system-error`: log append failing still returns the
        report; the entry is lost — best-effort."""
        parent_log = Log(
            session_id="sess-orch",
            entries=(
                registration_entry("sess-orch", "orchestrator"),
                resolution_entry("sess-orch", actor="reviewer"),
            ),
        )
        step_log = Log(
            session_id="sess-step",
            entries=(registration_entry("sess-step", "reviewer", "sess-orch"),),
        )
        resolver = StepInstructionResolver(
            workflow_catalog=workflow_catalog,
            session_log_store=failing_session_log_store(
                {"sess-orch": parent_log, "sess-step": step_log}
            ),
        )

        report = resolver.resolve_step_instructions(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "system-error"
        assert report.outcome.error.code == "log-append-failed"
