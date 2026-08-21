"""Shared tmp-dir workspace and configuration fixtures for the context resolvers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import Step, Workflow, WorkflowCatalog
from errors import StateError, SystemFailureError
from stores.session_log_store import (
    Context,
    Log,
    LogEntry,
    Outcome,
    Report,
    SessionLogStore,
)

CAPABILITIES = {
    "deep-reasoning": 1,
    "coding": 1,
    "tool-use": 1,
    "long-context": 1,
    "multimodal": 0,
    "writing-quality": 1,
    "instruction-following": 1,
    "fast-iteration": 1,
    "schema-adherence": 1,
}

INSTANCE_ID = "verification-01J9XQ"


def build_entry(
    timestamp: str,
    function: str,
    status: str,
    session_id: str,
    parent_session_id: str | None = None,
    workflow_instance_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> LogEntry:
    """Build one contract-valid log entry."""
    return LogEntry(
        timestamp=timestamp,
        report=Report(
            context=Context(
                function=function,
                session_id=session_id,
                parent_session_id=parent_session_id,
                workflow_instance_id=workflow_instance_id,
            ),
            outcome=Outcome(status=status),
            payload=payload or {},
        ),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Provide an empty tmp-dir workspace."""
    return tmp_path


@pytest.fixture
def session_log_store(workspace: Path) -> SessionLogStore:
    """Provide the real log store over the tmp-dir workspace."""
    return SessionLogStore(workspace)


@pytest.fixture
def workflow_catalog() -> WorkflowCatalog:
    """Provide a two-workflow catalog driven by one orchestrator.

    Both workflows share `workflow-selection-handling` / `workflow-selection`, so the
    resolvers' ordered de-duplication is observable.
    """
    review_step = Step(
        slug="review",
        actor="reviewer",
        artifact="story",
        instructions=("review-handoff",),
        capabilities=CAPABILITIES,
        skills=("code-review",),
    )
    pairing_step = Step(
        slug="pair",
        actor="developer",
        artifact="story",
        instructions=(),
        capabilities=CAPABILITIES,
        skills=(),
    )
    return WorkflowCatalog(
        workflows={
            "verification": Workflow(
                slug="verification",
                facilitator="orchestrator",
                steps=(review_step,),
                instructions=("workflow-selection-handling", "reports-handling"),
                skills=("workflow-selection", "verification-procedure"),
            ),
            "pair-programming": Workflow(
                slug="pair-programming",
                facilitator="orchestrator",
                steps=(pairing_step,),
                instructions=(
                    "workflow-selection-handling",
                    "step-resolution-handling",
                    "no-next-step-handling",
                ),
                skills=("workflow-selection", "pair-programming-procedure"),
            ),
        }
    )


@pytest.fixture
def register_session(session_log_store: SessionLogStore):
    """Provide a seeder writing function 0's registration entry for a session."""

    def _register(
        session_id: str,
        agent: str,
        parent_session_id: str | None = None,
        timestamp: str = "2026-08-18T09:00:00Z",
    ) -> None:
        session_log_store.create_session_log(
            build_entry(
                timestamp,
                "start-session",
                "started",
                session_id,
                parent_session_id,
                payload={
                    "session": {
                        "agent": agent,
                        "sessionId": session_id,
                        "parentSessionId": parent_session_id,
                    }
                },
            )
        )

    return _register


@pytest.fixture
def journal_resolution(session_log_store: SessionLogStore):
    """Provide a seeder appending a function 3 step-resolution entry."""

    def _journal(
        session_id: str,
        actor: str,
        step_slug: str = "review",
        workflow_instance_id: str = INSTANCE_ID,
        instructions: object = "stale-instruction",
        skills: object = "stale-skill",
        timestamp: str = "2026-08-18T09:01:00Z",
    ) -> None:
        session_log_store.append_log_entry(
            session_id,
            build_entry(
                timestamp,
                "resolve-step",
                "step-resolution",
                session_id,
                workflow_instance_id=workflow_instance_id,
                payload={
                    "step": {
                        "slug": step_slug,
                        "actor": actor,
                        "artifact": "story",
                        "instructions": instructions,
                        "skills": skills,
                        "capabilities": CAPABILITIES,
                    }
                },
            ),
        )

    return _journal


@pytest.fixture
def journal_postconditions(session_log_store: SessionLogStore):
    """Provide a seeder appending a function 10 outcome entry."""

    def _journal(
        session_id: str,
        workflow_instance_id: str = INSTANCE_ID,
        timestamp: str = "2026-08-18T09:02:00Z",
    ) -> None:
        session_log_store.append_log_entry(
            session_id,
            build_entry(
                timestamp,
                "check-step-postconditions",
                "pass",
                session_id,
                workflow_instance_id=workflow_instance_id,
                payload={"conditionChecks": []},
            ),
        )

    return _journal


@pytest.fixture
def end_session_log(session_log_store: SessionLogStore):
    """Provide a seeder appending function 11's ending entry."""

    def _end(session_id: str, timestamp: str = "2026-08-18T09:30:00Z") -> None:
        session_log_store.append_log_entry(
            session_id, build_entry(timestamp, "end-session", "ended", session_id)
        )

    return _end


@pytest.fixture
def read_journal(workspace: Path):
    """Provide a reader over one session log's raw entries."""

    def _read(session_id: str) -> list[dict]:
        path = workspace / "logs" / f"{session_id}.log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    return _read


@pytest.fixture
def orchestrator_session(register_session) -> str:
    """Provide a registered root orchestrator session."""
    register_session("sess-orch", "orchestrator")
    return "sess-orch"


@pytest.fixture
def step_session(register_session, journal_resolution) -> str:
    """Provide a registered step session correlated to its parent's resolution."""
    register_session("sess-orch", "orchestrator")
    journal_resolution("sess-orch", actor="reviewer")
    register_session("sess-step", "reviewer", parent_session_id="sess-orch")
    return "sess-step"


class _FailingSessionLogStore:
    """Fake store serving seeded logs whose appends fail like a broken environment."""

    def __init__(self, logs: dict[str, Log]) -> None:
        """Hold the logs a read serves, keyed by session id."""
        self._logs = logs

    def load_session_log(self, session_id: str) -> Log:
        """Serve the seeded log, or refuse like the real store does."""
        log = self._logs.get(session_id)
        if log is None:
            raise StateError(
                "session-unregistered", f"No session log exists for '{session_id}'.", False
            )
        return log

    def append_log_entry(self, session_id: str, entry: LogEntry) -> None:
        """Fail the way an unwritable log fails."""
        raise SystemFailureError("log-append-failed", "Log append failed.", True)


@pytest.fixture
def failing_session_log_store():
    """Provide the fake store type whose log appends fail."""
    return _FailingSessionLogStore


@pytest.fixture
def registration_entry():
    """Provide a builder for one session's registration entry."""

    def _build(session_id: str, agent: str, parent_session_id: str | None = None) -> LogEntry:
        return build_entry(
            "2026-08-18T09:00:00Z",
            "start-session",
            "started",
            session_id,
            parent_session_id,
            payload={
                "session": {
                    "agent": agent,
                    "sessionId": session_id,
                    "parentSessionId": parent_session_id,
                }
            },
        )

    return _build


@pytest.fixture
def resolution_entry():
    """Provide a builder for one function 3 step-resolution entry."""

    def _build(session_id: str, actor: str, step_slug: str = "review") -> LogEntry:
        return build_entry(
            "2026-08-18T09:01:00Z",
            "resolve-step",
            "step-resolution",
            session_id,
            workflow_instance_id=INSTANCE_ID,
            payload={
                "step": {
                    "slug": step_slug,
                    "actor": actor,
                    "artifact": "story",
                    "instructions": "stale-instruction",
                    "capabilities": CAPABILITIES,
                }
            },
        )

    return _build
