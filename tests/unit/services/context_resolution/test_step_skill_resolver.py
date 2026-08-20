"""Unit tests for the step skill resolver — harness function 7."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import WorkflowCatalog
from errors import InquiryError
from services.context_resolution import StepSkillResolver
from stores.session_log_store import Log, SessionLogStore

_INSTANCE_ID = "verification-01J9XQ"


@pytest.fixture
def resolver(
    workflow_catalog: WorkflowCatalog, session_log_store: SessionLogStore
) -> StepSkillResolver:
    """Provide the service under test with its injected collaborators."""
    return StepSkillResolver(
        workflow_catalog=workflow_catalog,
        session_log_store=session_log_store,
    )


class TestStepSkillResolver:
    """Cover harness function 7 (resolve-step-skills)."""

    def test_resolve_step_skills_returns_the_correlated_step_skills(
        self, resolver: StepSkillResolver, step_session: str
    ) -> None:
        """Spec function 7, invariant 1 + worked example: skill ids are declared per step —
        per step, not per workflow: a session loads exactly its step's skills."""
        report = resolver.resolve_step_skills(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert report.outcome.status == "resolved"
        assert report.outcome.error is None
        assert report.context.function == "resolve-step-skills"
        assert report.context.workflow_instance_id == _INSTANCE_ID
        assert report.skills == ("code-review",)

    def test_resolve_step_skills_journals_its_own_entry(
        self, resolver: StepSkillResolver, step_session: str, read_journal
    ) -> None:
        """Spec function 7, Postconditions: the invocation appends its own entry to the
        step session's log, alongside function 6's."""
        resolver.resolve_step_skills(session_id=step_session, parent_session_id="sess-orch")

        entries = read_journal(step_session)
        assert len(entries) == 2
        assert entries[1]["report"]["context"]["function"] == "resolve-step-skills"
        assert entries[1]["report"]["skills"] == ["code-review"]

    def test_resolve_step_skills_returns_an_empty_set_for_a_step_declaring_none(
        self, resolver: StepSkillResolver, register_session, journal_resolution
    ) -> None:
        """Spec function 7, invariant 1: a session loads exactly its step's declared
        skills — a step declaring none loads none."""
        register_session("sess-orch", "orchestrator")
        journal_resolution(
            "sess-orch",
            actor="developer",
            step_slug="pair",
            workflow_instance_id="pair-programming-01J9XR",
        )
        register_session("sess-step", "developer", parent_session_id="sess-orch")

        report = resolver.resolve_step_skills(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "resolved"
        assert report.skills == ()

    def test_resolve_step_skills_lets_the_step_declaration_decide(
        self, resolver: StepSkillResolver, step_session: str, read_journal
    ) -> None:
        """Spec function 7, invariant 3: resolution is deterministic — the step declaration
        decides, never the agent and never the journaled payload."""
        journaled_step = read_journal("sess-orch")[1]["report"]["step"]

        report = resolver.resolve_step_skills(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert journaled_step["skills"] == "stale-skill"
        assert report.skills == ("code-review",)

    def test_resolve_step_skills_correlates_on_the_session_agent(
        self,
        resolver: StepSkillResolver,
        register_session,
        journal_resolution,
        journal_postconditions,
    ) -> None:
        """Spec function 7, invariant 2: correlation identical to function 6 — the parent's
        latest unresolved `step-resolution` entry whose actor is the session's agent."""
        register_session("sess-orch", "orchestrator")
        journal_resolution("sess-orch", actor="developer", step_slug="pair")
        journal_postconditions("sess-orch")
        journal_resolution(
            "sess-orch", actor="reviewer", step_slug="review", timestamp="2026-08-18T09:03:00Z"
        )
        register_session("sess-step", "reviewer", parent_session_id="sess-orch")

        report = resolver.resolve_step_skills(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "resolved"
        assert report.skills == ("code-review",)

    def test_resolve_step_skills_refuses_a_session_with_no_correlation(
        self,
        resolver: StepSkillResolver,
        register_session,
        journal_resolution,
        read_journal,
    ) -> None:
        """Spec function 7, Preconditions (function 6's apply identically): no unresolved
        correlating `resolve-step` entry is `state-error` (`step-correlation-missing`),
        journaled."""
        register_session("sess-orch", "orchestrator")
        journal_resolution("sess-orch", actor="developer", step_slug="pair")
        register_session("sess-step", "reviewer", parent_session_id="sess-orch")

        report = resolver.resolve_step_skills(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "step-correlation-missing"
        assert report.skills is None
        entries = read_journal("sess-step")
        assert len(entries) == 2
        assert "skills" not in entries[1]["report"]

    def test_resolve_step_skills_refuses_a_concluded_correlation(
        self,
        resolver: StepSkillResolver,
        step_session: str,
        journal_postconditions,
        read_journal,
    ) -> None:
        """Spec function 7, Preconditions (function 6's apply identically): a resolution
        carrying a later function-10 outcome no longer correlates —
        `state-error` (`step-correlation-missing`), journaled."""
        journal_postconditions("sess-orch")

        report = resolver.resolve_step_skills(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "step-correlation-missing"
        assert len(read_journal(step_session)) == 2

    def test_resolve_step_skills_refuses_an_unregistered_session(
        self, resolver: StepSkillResolver, workspace: Path
    ) -> None:
        """Spec function 7, Preconditions + Outcomes rule 4: an unregistered session is
        `inquiry-error` (`session-unregistered`), with no log to journal to."""
        report = resolver.resolve_step_skills(
            session_id="sess-unknown", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error.code == "session-unregistered"
        assert report.skills is None
        assert not (workspace / "logs" / "sess-unknown.log.jsonl").exists()

    def test_resolve_step_skills_reports_a_configuration_error_for_an_absent_step(
        self,
        resolver: StepSkillResolver,
        register_session,
        journal_resolution,
        read_journal,
    ) -> None:
        """Spec Outcomes rule 1, `configuration-error`: configuration invalid at use time —
        a correlated step the loaded workflow no longer declares, journaled."""
        register_session("sess-orch", "orchestrator")
        journal_resolution("sess-orch", actor="reviewer", step_slug="ghost")
        register_session("sess-step", "reviewer", parent_session_id="sess-orch")

        report = resolver.resolve_step_skills(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "configuration-error"
        assert report.skills is None
        assert len(read_journal("sess-step")) == 2

    def test_resolve_step_skills_rejects_a_non_slug_session_id(
        self, resolver: StepSkillResolver, workspace: Path
    ) -> None:
        """Spec Outcomes rule 1 + rule 4: a non-slug `sessionId` is `inquiry-error`
        (`invalid-inquiry`) — unjournalable."""
        with pytest.raises(InquiryError) as raised:
            resolver.resolve_step_skills(session_id="../escape", parent_session_id="sess-orch")

        assert raised.value.code == "invalid-inquiry"
        assert not (workspace / "logs").exists()

    def test_resolve_step_skills_refuses_an_ended_session(
        self, resolver: StepSkillResolver, step_session: str, end_session_log, read_journal
    ) -> None:
        """Spec C8 + Outcomes rule 3: a call against an ended session is `state-error`
        (`session-ended`), never journaled."""
        end_session_log(step_session)

        report = resolver.resolve_step_skills(
            session_id=step_session, parent_session_id="sess-orch"
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-ended"
        assert len(read_journal(step_session)) == 2

    def test_resolve_step_skills_reports_a_system_error_on_append_failure(
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
        resolver = StepSkillResolver(
            workflow_catalog=workflow_catalog,
            session_log_store=failing_session_log_store(
                {"sess-orch": parent_log, "sess-step": step_log}
            ),
        )

        report = resolver.resolve_step_skills(
            session_id="sess-step", parent_session_id="sess-orch"
        )

        assert report.outcome.status == "system-error"
        assert report.outcome.error.code == "log-append-failed"
