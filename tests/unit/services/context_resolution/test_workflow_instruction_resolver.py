"""Unit tests for the workflow instruction resolver — harness function 1."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import WorkflowCatalog
from errors import InquiryError
from services.context_resolution import WorkflowInstructionResolver
from stores.session_log_store import Log, SessionLogStore

_EXPECTED_INSTRUCTIONS = (
    "workflow-selection-handling",
    "reports-handling",
    "step-resolution-handling",
)


@pytest.fixture
def resolver(
    workflow_catalog: WorkflowCatalog, session_log_store: SessionLogStore
) -> WorkflowInstructionResolver:
    """Provide the service under test with its injected collaborators."""
    return WorkflowInstructionResolver(
        workflow_catalog=workflow_catalog,
        session_log_store=session_log_store,
    )


class TestWorkflowInstructionResolver:
    """Cover harness function 1 (resolve-workflow-instructions)."""

    def test_resolve_workflow_instructions_returns_the_orchestrator_instruction_refs(
        self, resolver: WorkflowInstructionResolver, orchestrator_session: str
    ) -> None:
        """Spec function 1, invariants 1–2 + worked example: the instruction set derives
        from configuration only, keyed by orchestrator — one ref per declared duty, in
        catalog order, each duty named once."""
        report = resolver.resolve_workflow_instructions(
            session_id=orchestrator_session, parent_session_id=None
        )

        assert report.outcome.status == "resolved"
        assert report.outcome.error is None
        assert report.context.function == "resolve-workflow-instructions"
        assert report.context.session_id == orchestrator_session
        assert report.context.workflow_instance_id is None
        assert report.instructions == _EXPECTED_INSTRUCTIONS

    def test_resolve_workflow_instructions_journals_its_own_entry(
        self, resolver: WorkflowInstructionResolver, orchestrator_session: str, read_journal
    ) -> None:
        """Spec function 1, Postconditions: the invocation appends its own entry to the
        session's log — what was resolved, for which orchestrator."""
        resolver.resolve_workflow_instructions(
            session_id=orchestrator_session, parent_session_id=None
        )

        entries = read_journal(orchestrator_session)
        assert len(entries) == 2
        assert entries[1]["report"]["context"]["function"] == "resolve-workflow-instructions"
        assert entries[1]["report"]["instructions"] == list(_EXPECTED_INSTRUCTIONS)

    def test_resolve_workflow_instructions_lets_configuration_alone_decide(
        self, resolver: WorkflowInstructionResolver, orchestrator_session: str, register_session
    ) -> None:
        """Spec function 1, invariant 3: resolution is deterministic and orchestrator-scoped
        — the configuration decides the refs, never the agent and never the log."""
        register_session("sess-orch-2", "orchestrator")

        first = resolver.resolve_workflow_instructions(
            session_id=orchestrator_session, parent_session_id=None
        )
        again = resolver.resolve_workflow_instructions(
            session_id=orchestrator_session, parent_session_id=None
        )
        other_session = resolver.resolve_workflow_instructions(
            session_id="sess-orch-2", parent_session_id=None
        )

        assert first.instructions == again.instructions == other_session.instructions

    def test_resolve_workflow_instructions_refuses_a_step_session(
        self, resolver: WorkflowInstructionResolver, step_session: str, read_journal
    ) -> None:
        """Spec function 1, precondition (E): a session with an unresolved `resolve-step`
        entry correlating to it is a step session and loads functions 6–7 instead —
        `state-error` (`session-kind-mismatch`), journaled."""
        report = resolver.resolve_workflow_instructions(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-kind-mismatch"
        assert report.outcome.error.message
        assert report.instructions is None
        entries = read_journal(step_session)
        assert len(entries) == 2
        assert entries[1]["report"]["outcome"]["status"] == "state-error"
        assert "instructions" not in entries[1]["report"]

    def test_resolve_workflow_instructions_refuses_an_unregistered_session(
        self, resolver: WorkflowInstructionResolver, workspace: Path
    ) -> None:
        """Spec function 1, precondition (E) + Outcomes rule 4: an unregistered session is
        `inquiry-error` (`session-unregistered`) — the report is returned, but there is no
        log to journal it to."""
        report = resolver.resolve_workflow_instructions(
            session_id="sess-unknown", parent_session_id=None
        )

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error.code == "session-unregistered"
        assert report.instructions is None
        assert not (workspace / "logs" / "sess-unknown.log.jsonl").exists()

    def test_resolve_workflow_instructions_refuses_a_log_without_a_registration(
        self,
        resolver: WorkflowInstructionResolver,
        session_log_store: SessionLogStore,
        resolution_entry,
        read_journal,
    ) -> None:
        """Spec function 1, precondition (E): the session is registered — a start entry
        exists; a log carrying none is `inquiry-error` (`session-unregistered`), and
        rule 4 leaves it unjournaled."""
        session_log_store.create_session_log(resolution_entry("sess-orphan", actor="reviewer"))

        report = resolver.resolve_workflow_instructions(
            session_id="sess-orphan", parent_session_id=None
        )

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error.code == "session-unregistered"
        assert len(read_journal("sess-orphan")) == 1

    def test_resolve_workflow_instructions_rejects_a_non_slug_session_id(
        self, resolver: WorkflowInstructionResolver, workspace: Path
    ) -> None:
        """Spec Outcomes rule 1 + rule 4: a non-slug `sessionId` is `inquiry-error`
        (`invalid-inquiry`) — unjournalable, so no contract-valid report can be built."""
        with pytest.raises(InquiryError) as raised:
            resolver.resolve_workflow_instructions(
                session_id="../escape", parent_session_id=None
            )

        assert raised.value.code == "invalid-inquiry"
        assert not (workspace / "logs").exists()

    def test_resolve_workflow_instructions_refuses_an_ended_session(
        self,
        resolver: WorkflowInstructionResolver,
        orchestrator_session: str,
        end_session_log,
        read_journal,
    ) -> None:
        """Spec C8 + Outcomes rule 3: a call against a session whose log carries an ending
        entry is `state-error` (`session-ended`) and is never journaled — no entry ever
        follows the ending entry."""
        end_session_log(orchestrator_session)

        report = resolver.resolve_workflow_instructions(
            session_id=orchestrator_session, parent_session_id=None
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-ended"
        assert len(read_journal(orchestrator_session)) == 2

    def test_resolve_workflow_instructions_reports_a_system_error_on_append_failure(
        self,
        workflow_catalog: WorkflowCatalog,
        failing_session_log_store,
        registration_entry,
    ) -> None:
        """Spec Outcomes rule 1, `system-error`: log append failing still returns the
        report; the entry is lost — best-effort."""
        log = Log(
            session_id="sess-orch",
            entries=(registration_entry("sess-orch", "orchestrator"),),
        )
        resolver = WorkflowInstructionResolver(
            workflow_catalog=workflow_catalog,
            session_log_store=failing_session_log_store({"sess-orch": log}),
        )

        report = resolver.resolve_workflow_instructions(
            session_id="sess-orch", parent_session_id=None
        )

        assert report.outcome.status == "system-error"
        assert report.outcome.error.code == "log-append-failed"
