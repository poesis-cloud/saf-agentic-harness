"""Unit tests for the workflow skill resolver — harness function 2."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import WorkflowCatalog
from errors import InquiryError
from services.context_resolution import WorkflowSkillResolver
from stores.session_log_store import Log, SessionLogStore

_EXPECTED_SKILLS = (
    "workflow-selection",
    "verification-procedure",
    "pair-programming-procedure",
)


@pytest.fixture
def resolver(
    workflow_catalog: WorkflowCatalog, session_log_store: SessionLogStore
) -> WorkflowSkillResolver:
    """Provide the service under test with its injected collaborators."""
    return WorkflowSkillResolver(
        workflow_catalog=workflow_catalog,
        session_log_store=session_log_store,
    )


class TestWorkflowSkillResolver:
    """Cover harness function 2 (resolve-workflow-skills)."""

    def test_resolve_workflow_skills_returns_the_orchestrator_skill_set(
        self, resolver: WorkflowSkillResolver, orchestrator_session: str
    ) -> None:
        """Spec function 2, invariant 1 + worked example: the skill set derives from
        configuration only — the selection skill plus each facilitated workflow's
        procedure skill."""
        report = resolver.resolve_workflow_skills(
            session_id=orchestrator_session, parent_session_id=None
        )

        assert report.outcome.status == "resolved"
        assert report.outcome.error is None
        assert report.context.function == "resolve-workflow-skills"
        assert report.context.workflow_instance_id is None
        assert report.skills == _EXPECTED_SKILLS

    def test_resolve_workflow_skills_journals_its_own_entry(
        self, resolver: WorkflowSkillResolver, orchestrator_session: str, read_journal
    ) -> None:
        """Spec function 2, Postconditions: the invocation appends its own entry to the
        session's log, alongside function 1's."""
        resolver.resolve_workflow_skills(
            session_id=orchestrator_session, parent_session_id=None
        )

        entries = read_journal(orchestrator_session)
        assert len(entries) == 2
        assert entries[1]["report"]["context"]["function"] == "resolve-workflow-skills"
        assert entries[1]["report"]["skills"] == list(_EXPECTED_SKILLS)

    def test_resolve_workflow_skills_lets_configuration_alone_decide(
        self, resolver: WorkflowSkillResolver, orchestrator_session: str, register_session
    ) -> None:
        """Spec function 2, invariant 2: resolution is deterministic — the configuration
        decides, never the agent."""
        register_session("sess-orch-2", "orchestrator")

        first = resolver.resolve_workflow_skills(
            session_id=orchestrator_session, parent_session_id=None
        )
        again = resolver.resolve_workflow_skills(
            session_id=orchestrator_session, parent_session_id=None
        )
        other_session = resolver.resolve_workflow_skills(
            session_id="sess-orch-2", parent_session_id=None
        )

        assert first.skills == again.skills == other_session.skills

    def test_resolve_workflow_skills_refuses_a_step_session(
        self, resolver: WorkflowSkillResolver, step_session: str, read_journal
    ) -> None:
        """Spec function 2, Preconditions (function 1's apply identically): a step session
        is `state-error` (`session-kind-mismatch`), journaled."""
        report = resolver.resolve_workflow_skills(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-kind-mismatch"
        assert report.skills is None
        entries = read_journal(step_session)
        assert len(entries) == 2
        assert "skills" not in entries[1]["report"]

    def test_resolve_workflow_skills_refuses_an_unregistered_session(
        self, resolver: WorkflowSkillResolver, workspace: Path
    ) -> None:
        """Spec function 2, Preconditions + Outcomes rule 4: an unregistered session is
        `inquiry-error` (`session-unregistered`), with no log to journal to."""
        report = resolver.resolve_workflow_skills(
            session_id="sess-unknown", parent_session_id=None
        )

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error.code == "session-unregistered"
        assert report.skills is None
        assert not (workspace / "logs" / "sess-unknown.log.jsonl").exists()

    def test_resolve_workflow_skills_rejects_a_non_slug_session_id(
        self, resolver: WorkflowSkillResolver, workspace: Path
    ) -> None:
        """Spec Outcomes rule 1 + rule 4: a non-slug `sessionId` is `inquiry-error`
        (`invalid-inquiry`) — unjournalable."""
        with pytest.raises(InquiryError) as raised:
            resolver.resolve_workflow_skills(session_id="../escape", parent_session_id=None)

        assert raised.value.code == "invalid-inquiry"
        assert not (workspace / "logs").exists()

    def test_resolve_workflow_skills_refuses_an_ended_session(
        self,
        resolver: WorkflowSkillResolver,
        orchestrator_session: str,
        end_session_log,
        read_journal,
    ) -> None:
        """Spec C8 + Outcomes rule 3: a call against an ended session is `state-error`
        (`session-ended`), never journaled."""
        end_session_log(orchestrator_session)

        report = resolver.resolve_workflow_skills(
            session_id=orchestrator_session, parent_session_id=None
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-ended"
        assert len(read_journal(orchestrator_session)) == 2

    def test_resolve_workflow_skills_reports_a_system_error_on_append_failure(
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
        resolver = WorkflowSkillResolver(
            workflow_catalog=workflow_catalog,
            session_log_store=failing_session_log_store({"sess-orch": log}),
        )

        report = resolver.resolve_workflow_skills(
            session_id="sess-orch", parent_session_id=None
        )

        assert report.outcome.status == "system-error"
        assert report.outcome.error.code == "log-append-failed"
