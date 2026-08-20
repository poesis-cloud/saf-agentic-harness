"""Unit tests for the session lifecycle service — harness functions 0 and 11."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config import AccessControlList
from errors import InquiryError, StateError, SystemFailureError
from services.session_lifecycle import SessionLifecycle
from stores.session_log_store import (
    Context,
    Log,
    LogEntry,
    Outcome,
    Report,
    SessionLogStore,
)

_CAPABILITIES = {
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

_ACL = AccessControlList(
    grants={"orchestrator": frozenset(), "reviewer": frozenset()}
)


def _entry(
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


def _start_entry(
    session_id: str,
    agent: str,
    parent_session_id: str | None = None,
    timestamp: str = "2026-08-18T09:00:00Z",
) -> LogEntry:
    """Build function 0's registration entry."""
    return _entry(
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


def _resolution_entry(
    session_id: str,
    actor: str,
    step_slug: str = "review",
    workflow_instance_id: str = "verification-01J9XQ",
    timestamp: str = "2026-08-18T09:01:00Z",
) -> LogEntry:
    """Build a function 3 step-resolution entry naming its actor."""
    return _entry(
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
                "instructions": "do-work",
                "capabilities": _CAPABILITIES,
            }
        },
    )


def _postconditions_entry(
    session_id: str,
    workflow_instance_id: str = "verification-01J9XQ",
    timestamp: str = "2026-08-18T09:02:00Z",
) -> LogEntry:
    """Build a function 10 outcome entry concluding the in-flight step."""
    return _entry(
        timestamp,
        "check-step-postconditions",
        "pass",
        session_id,
        workflow_instance_id=workflow_instance_id,
        payload={"conditionChecks": []},
    )


def _ending_entry(
    session_id: str,
    parent_session_id: str | None = None,
    timestamp: str = "2026-08-18T09:30:00Z",
) -> LogEntry:
    """Build function 11's ending entry."""
    return _entry(timestamp, "end-session", "ended", session_id, parent_session_id)


class _FailingSessionLogStore:
    """Fake store whose log writes fail the way a broken environment does."""

    def __init__(self, log: Log | None = None) -> None:
        """Hold the log a read returns, or none for an unregistered session."""
        self._log = log

    def load_session_log(self, session_id: str) -> Log:
        """Return the seeded log, or refuse like the real store does."""
        if self._log is None:
            raise StateError(
                "session-unregistered", f"No session log exists for '{session_id}'.", False
            )
        return self._log

    def create_session_log(self, entry: LogEntry) -> Log:
        """Fail the way a full disk fails."""
        raise SystemFailureError("log-creation-failed", "Log creation failed.", True)

    def append_log_entry(self, session_id: str, entry: LogEntry) -> None:
        """Fail the way an unwritable log fails."""
        raise SystemFailureError("log-append-failed", "Log append failed.", True)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Provide an empty tmp-dir workspace."""
    return tmp_path


@pytest.fixture
def session_log_store(workspace: Path) -> SessionLogStore:
    """Provide the real log store over the tmp-dir workspace."""
    return SessionLogStore(workspace)


@pytest.fixture
def lifecycle(session_log_store: SessionLogStore) -> SessionLifecycle:
    """Provide the service under test with its injected collaborators."""
    return SessionLifecycle(
        session_log_store=session_log_store,
        access_control_list=_ACL,
    )


@pytest.fixture
def read_journal(workspace: Path):
    """Provide a reader over one session log's raw entries."""

    def _read(session_id: str) -> list[dict]:
        path = workspace / "logs" / f"{session_id}.log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    return _read


class TestSessionLifecycle:
    """Cover harness functions 0 (start-session) and 11 (end-session)."""

    def test_start_session_registers_a_root_session_of_a_declared_actor(
        self, lifecycle: SessionLifecycle
    ) -> None:
        """Spec function 0, Interface + worked example: a root session of a declared
        actor returns `started` with the session object naming agent and ids."""
        report = lifecycle.start_session(
            agent="orchestrator", session_id="sess-root", parent_session_id=None
        )

        assert report.outcome.status == "started"
        assert report.outcome.error is None
        assert report.context.function == "start-session"
        assert report.context.session_id == "sess-root"
        assert report.context.parent_session_id is None
        assert report.context.workflow_instance_id is None
        assert report.session.agent == "orchestrator"
        assert report.session.session_id == "sess-root"
        assert report.session.parent_session_id is None

    def test_start_session_creates_the_log_with_the_start_as_its_first_line(
        self, lifecycle: SessionLifecycle, workspace: Path, read_journal
    ) -> None:
        """Spec function 0, invariant 1 + Postconditions: starting precedes everything —
        function 0 creates the very file the others append to, its entry the first line."""
        assert not (workspace / "logs" / "sess-root.log.jsonl").exists()

        lifecycle.start_session(
            agent="orchestrator", session_id="sess-root", parent_session_id=None
        )

        entries = read_journal("sess-root")
        assert len(entries) == 1
        assert entries[0]["report"]["context"]["function"] == "start-session"
        assert entries[0]["report"]["outcome"]["status"] == "started"
        assert entries[0]["report"]["session"]["agent"] == "orchestrator"

    def test_start_session_passes_through_a_root_session_of_no_framework_agent(
        self, lifecycle: SessionLifecycle, workspace: Path
    ) -> None:
        """Spec function 0, precondition (E) — the ACL membership gate: a root session
        whose `agent` names no framework agent is `not-applicable`, never journaled
        (Outcomes rule 2)."""
        report = lifecycle.start_session(
            agent="some-host-agent", session_id="sess-foreign", parent_session_id=None
        )

        assert report.outcome.status == "not-applicable"
        assert report.outcome.error is None
        assert report.session is None
        assert not (workspace / "logs" / "sess-foreign.log.jsonl").exists()

    def test_start_session_registers_a_step_session_correlated_to_its_parent(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore
    ) -> None:
        """Spec function 0, precondition (E) — the correlation gate: for a step session
        the parent's unresolved step resolution naming this agent IS the check."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))
        session_log_store.append_log_entry(
            "sess-root", _resolution_entry("sess-root", actor="reviewer")
        )

        report = lifecycle.start_session(
            agent="reviewer", session_id="sess-step", parent_session_id="sess-root"
        )

        assert report.outcome.status == "started"
        assert report.session.parent_session_id == "sess-root"
        assert report.context.parent_session_id == "sess-root"

    def test_start_session_passes_through_a_step_session_with_no_correlation(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore, workspace: Path
    ) -> None:
        """Spec function 0, precondition (E): a foreign dispatch finds no correlation to
        complete — `not-applicable`, never journaled (Outcomes rule 2)."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))
        session_log_store.append_log_entry(
            "sess-root", _resolution_entry("sess-root", actor="reviewer")
        )

        report = lifecycle.start_session(
            agent="intruder", session_id="sess-step", parent_session_id="sess-root"
        )

        assert report.outcome.status == "not-applicable"
        assert report.session is None
        assert not (workspace / "logs" / "sess-step.log.jsonl").exists()

    def test_start_session_passes_through_a_step_session_whose_resolution_concluded(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore, workspace: Path
    ) -> None:
        """Spec function 0, precondition (E): the correlation must be UNRESOLVED — a
        resolution already carrying a function-10 outcome names no target."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))
        session_log_store.append_log_entry(
            "sess-root", _resolution_entry("sess-root", actor="reviewer")
        )
        session_log_store.append_log_entry("sess-root", _postconditions_entry("sess-root"))

        report = lifecycle.start_session(
            agent="reviewer", session_id="sess-step", parent_session_id="sess-root"
        )

        assert report.outcome.status == "not-applicable"
        assert not (workspace / "logs" / "sess-step.log.jsonl").exists()

    def test_start_session_replays_the_registration_of_an_open_session(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore, read_journal
    ) -> None:
        """Spec function 0, invariant 4: starting is idempotent per session — the duplicate
        call returns the same `started` report, rebuilt from the existing registration
        entry, with no second entry."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))

        report = lifecycle.start_session(
            agent="orchestrator", session_id="sess-root", parent_session_id=None
        )

        assert report.outcome.status == "started"
        assert report.session.agent == "orchestrator"
        assert len(read_journal("sess-root")) == 1

    def test_start_session_refuses_a_re_registration_naming_a_different_agent(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore, read_journal
    ) -> None:
        """Spec function 0, invariant 4: a re-registration naming a DIFFERENT agent is
        `state-error` (`session-conflict`), journaled — identity never silently mutates."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))

        report = lifecycle.start_session(
            agent="reviewer", session_id="sess-root", parent_session_id=None
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-conflict"
        assert report.outcome.error.message
        assert report.session is None
        entries = read_journal("sess-root")
        assert len(entries) == 2
        assert entries[1]["report"]["outcome"]["status"] == "state-error"
        assert "session" not in entries[1]["report"]

    def test_start_session_records_one_parent_per_start_for_unbounded_nesting(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore, read_journal
    ) -> None:
        """Spec function 0, invariant 3: the parent chain is unbounded — each start records
        one parent, so any nesting depth reconstructs by walking starts parent-by-parent."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))
        session_log_store.append_log_entry(
            "sess-root", _resolution_entry("sess-root", actor="reviewer")
        )
        lifecycle.start_session(
            agent="reviewer", session_id="sess-a", parent_session_id="sess-root"
        )
        session_log_store.append_log_entry(
            "sess-a",
            _resolution_entry(
                "sess-a", actor="auditor", step_slug="audit", timestamp="2026-08-18T09:05:00Z"
            ),
        )

        report = lifecycle.start_session(
            agent="auditor", session_id="sess-b", parent_session_id="sess-a"
        )

        assert report.outcome.status == "started"
        chain = []
        session_id: str | None = "sess-b"
        while session_id is not None:
            registration = read_journal(session_id)[0]["report"]["session"]
            chain.append(session_id)
            session_id = registration["parentSessionId"]
        assert chain == ["sess-b", "sess-a", "sess-root"]

    def test_start_session_rejects_a_non_slug_session_id(
        self, lifecycle: SessionLifecycle, workspace: Path
    ) -> None:
        """Spec Outcomes rule 1 + rule 4: a non-slug `sessionId` is `inquiry-error`
        (`invalid-inquiry`) — unjournalable, so no contract-valid report can be built."""
        with pytest.raises(InquiryError) as raised:
            lifecycle.start_session(
                agent="orchestrator", session_id="../escape", parent_session_id=None
            )

        assert raised.value.code == "invalid-inquiry"
        assert raised.value.message
        assert not (workspace / "logs").exists()

    def test_start_session_rejects_a_missing_agent(
        self, lifecycle: SessionLifecycle, workspace: Path
    ) -> None:
        """Spec Outcomes rule 1: a missing `agent` is `inquiry-error` (`invalid-inquiry`) —
        function 0's one required function-specific field."""
        with pytest.raises(InquiryError) as raised:
            lifecycle.start_session(agent="", session_id="sess-root", parent_session_id=None)

        assert raised.value.code == "invalid-inquiry"
        assert not (workspace / "logs").exists()

    def test_start_session_refuses_a_session_whose_log_carries_an_ending_entry(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore, read_journal
    ) -> None:
        """Spec C8 + Outcomes rule 3 + function 11 invariant 1: a call against an ended
        session is `state-error` (`session-ended`), never journaled — no entry ever follows
        the ending entry — and the refusal takes precedence over the idempotent replay."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))
        session_log_store.append_log_entry("sess-root", _ending_entry("sess-root"))

        report = lifecycle.start_session(
            agent="orchestrator", session_id="sess-root", parent_session_id=None
        )

        assert report.outcome.status == "state-error"
        assert report.outcome.error.code == "session-ended"
        assert report.session is None
        assert len(read_journal("sess-root")) == 2

    def test_start_session_reports_a_system_error_when_log_creation_fails(self) -> None:
        """Spec Outcomes rule 1, `system-error`: the environment fails (log creation
        failing) — the report is still returned; the entry is lost."""
        lifecycle = SessionLifecycle(
            session_log_store=_FailingSessionLogStore(),
            access_control_list=_ACL,
        )

        report = lifecycle.start_session(
            agent="orchestrator", session_id="sess-root", parent_session_id=None
        )

        assert report.outcome.status == "system-error"
        assert report.outcome.error.code == "log-creation-failed"
        assert report.session is None

    def test_end_session_closes_an_open_log_with_a_final_entry(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore, read_journal
    ) -> None:
        """Spec function 11, Postconditions + worked example: the session's log carries an
        ending entry as its last line, and the report recovers the parent from the start
        entry — `sessionId` is the whole inquiry."""
        session_log_store.create_session_log(
            _start_entry("sess-step", "reviewer", parent_session_id="sess-root")
        )

        report = lifecycle.end_session(session_id="sess-step")

        assert report.outcome.status == "ended"
        assert report.outcome.error is None
        assert report.context.function == "end-session"
        assert report.context.parent_session_id == "sess-root"
        entries = read_journal("sess-step")
        assert len(entries) == 2
        assert entries[-1]["report"]["context"]["function"] == "end-session"
        assert entries[-1]["report"]["outcome"]["status"] == "ended"

    def test_end_session_appends_no_second_ending_entry(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore, read_journal
    ) -> None:
        """Spec function 11, invariant 2: re-delivery of the same session-end signal appends
        no second ending entry and returns the same `ended` outcome."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))
        session_log_store.append_log_entry("sess-root", _ending_entry("sess-root"))

        report = lifecycle.end_session(session_id="sess-root")

        assert report.outcome.status == "ended"
        assert len(read_journal("sess-root")) == 2

    def test_end_session_is_exempt_from_the_ended_session_refusal(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore
    ) -> None:
        """Spec C8: functions 0–10 refuse an ended session; function 11 is explicitly
        exempt — its idempotent no-op IS the specified answer, never `session-ended`."""
        session_log_store.create_session_log(_start_entry("sess-root", "orchestrator"))
        session_log_store.append_log_entry("sess-root", _ending_entry("sess-root"))

        report = lifecycle.end_session(session_id="sess-root")

        assert report.outcome.status == "ended"
        assert report.outcome.error is None

    def test_end_session_is_a_no_op_for_a_never_started_session(
        self, lifecycle: SessionLifecycle, workspace: Path
    ) -> None:
        """Spec function 11, invariant 2: an ending call against a never-started session is
        an idempotent no-op — the same `ended` outcome, no entry, and never an error."""
        report = lifecycle.end_session(session_id="sess-unknown")

        assert report.outcome.status == "ended"
        assert report.outcome.error is None
        assert not (workspace / "logs" / "sess-unknown.log.jsonl").exists()

    def test_end_session_asserts_nothing_about_the_workflow_instance(
        self, lifecycle: SessionLifecycle, session_log_store: SessionLogStore
    ) -> None:
        """Spec function 11, invariant 4: ending asserts nothing about the session's
        workflow instance — the ending context carries no instance id."""
        session_log_store.create_session_log(
            _start_entry("sess-step", "reviewer", parent_session_id="sess-root")
        )
        session_log_store.append_log_entry(
            "sess-step", _resolution_entry("sess-step", actor="reviewer")
        )

        report = lifecycle.end_session(session_id="sess-step")

        assert report.outcome.status == "ended"
        assert report.context.workflow_instance_id is None

    def test_end_session_rejects_a_non_slug_session_id(
        self, lifecycle: SessionLifecycle, workspace: Path
    ) -> None:
        """Spec Outcomes rule 1 + rule 4: a non-slug `sessionId` is `inquiry-error`
        (`invalid-inquiry`) — unjournalable, surfacing at the command exit plane."""
        with pytest.raises(InquiryError) as raised:
            lifecycle.end_session(session_id="../escape")

        assert raised.value.code == "invalid-inquiry"
        assert not (workspace / "logs").exists()

    def test_end_session_reports_a_system_error_when_the_append_fails(self) -> None:
        """Spec Outcomes rule 1, `system-error`: log append failing still returns the
        report; the entry is lost — best-effort."""
        log = Log(session_id="sess-root", entries=(_start_entry("sess-root", "orchestrator"),))
        lifecycle = SessionLifecycle(
            session_log_store=_FailingSessionLogStore(log),
            access_control_list=_ACL,
        )

        report = lifecycle.end_session(session_id="sess-root")

        assert report.outcome.status == "system-error"
        assert report.outcome.error.code == "log-append-failed"
