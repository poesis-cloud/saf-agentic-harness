"""Unit tests for the session log store package."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from errors import StateError
from stores.session_log_store import (
    Context,
    Error,
    Log,
    LogEntry,
    Outcome,
    Report,
    SessionLogStore,
    WorkflowInstanceView,
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


def _report(
    function: str,
    status: str,
    session_id: str = "sess-1",
    workflow_instance_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> Report:
    return Report(
        context=Context(
            function=function,
            session_id=session_id,
            parent_session_id=None,
            workflow_instance_id=workflow_instance_id,
        ),
        outcome=Outcome(status=status),
        payload=payload or {},
    )


def _entry(
    timestamp: str,
    function: str,
    status: str,
    session_id: str = "sess-1",
    workflow_instance_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> LogEntry:
    return LogEntry(
        timestamp=timestamp,
        report=_report(function, status, session_id, workflow_instance_id, payload),
    )


def _start_entry(session_id: str = "sess-1", timestamp: str = "2026-08-17T13:00:00Z") -> LogEntry:
    return _entry(
        timestamp,
        "start-session",
        "started",
        session_id=session_id,
        payload={"session": {"agent": "agent-a", "sessionId": session_id, "parentSessionId": None}},
    )


def _step(slug: str, actor: str = "agent-a") -> dict[str, object]:
    return {
        "slug": slug,
        "actor": actor,
        "artifact": "sample",
        "instructions": "do-work",
        "capabilities": _CAPABILITIES,
        "conditions": [],
    }


def _resolution(
    timestamp: str,
    workflow_instance_id: str,
    step_slug: str,
    actor: str = "agent-a",
    session_id: str = "sess-1",
) -> LogEntry:
    return _entry(
        timestamp,
        "resolve-step",
        "step-resolution",
        session_id=session_id,
        workflow_instance_id=workflow_instance_id,
        payload={"step": _step(step_slug, actor)},
    )


def _postconditions(
    timestamp: str,
    workflow_instance_id: str,
    status: str,
    session_id: str = "sess-1",
) -> LogEntry:
    return _entry(
        timestamp,
        "check-step-postconditions",
        status,
        session_id=session_id,
        workflow_instance_id=workflow_instance_id,
        payload={"conditionChecks": []},
    )


class TestContext:
    def test_renders_contract_keys(self) -> None:
        context = Context("start-session", "sess-1", None, "flow-01AB")

        assert context.to_dict() == {
            "function": "start-session",
            "sessionId": "sess-1",
            "parentSessionId": None,
            "workflowInstanceId": "flow-01AB",
        }


class TestError:
    def test_renders_optional_retryable(self) -> None:
        assert Error("bad", "Bad", True).to_dict() == {
            "code": "bad",
            "message": "Bad",
            "retryable": True,
        }


class TestOutcome:
    def test_renders_error_when_present(self) -> None:
        outcome = Outcome("state-error", Error("bad", "Bad", False))

        assert outcome.to_dict()["error"]["code"] == "bad"


class TestReport:
    def test_renders_context_outcome_and_payload(self) -> None:
        report = _report("end-session", "ended", payload={"extra": 1})

        assert report.to_dict()["extra"] == 1


class TestLogEntry:
    def test_round_trips_from_contract_dict(self) -> None:
        entry = _start_entry()

        assert LogEntry.from_dict(entry.to_dict()) == entry


class TestLog:
    def test_exposes_session_and_entries(self) -> None:
        entry = _start_entry()
        log = Log("sess-1", (entry,))

        assert log.session_id == "sess-1"
        assert log.entries == (entry,)


class TestWorkflowInstanceView:
    def test_queries_executed_steps_latest_outcomes_and_unresolved_resolution(self) -> None:
        view = WorkflowInstanceView(
            "flow-01AB",
            (
                _resolution("2026-08-17T13:00:01Z", "flow-01AB", "alpha"),
                _postconditions("2026-08-17T13:00:02Z", "flow-01AB", "pass"),
                _resolution("2026-08-17T13:00:03Z", "flow-01AB", "alpha"),
                _postconditions("2026-08-17T13:00:04Z", "flow-01AB", "fail"),
                _resolution("2026-08-17T13:00:05Z", "flow-01AB", "beta", actor="agent-b"),
            ),
        )

        assert view.list_executed_steps() == frozenset()
        assert view.find_latest_outcome("alpha").report.outcome.status == "fail"
        assert view.find_unresolved_step_resolution("agent-b").report.payload["step"]["slug"] == "beta"


class TestSessionLogStore:
    def test_create_append_load_round_trip_byte_stability(self, tmp_path: Path) -> None:
        store = SessionLogStore(tmp_path)
        start = _start_entry()
        end = _entry("2026-08-17T13:00:01Z", "end-session", "ended")

        assert store.create_session_log(start) == Log("sess-1", (start,))
        store.append_log_entry("sess-1", end)
        log = store.load_session_log("sess-1")

        path = tmp_path / "logs" / "sess-1.log.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        rendered = [json.dumps(entry.to_dict(), ensure_ascii=False, separators=(",", ":")) for entry in log.entries]
        assert rendered == lines

    def test_create_refuses_to_overwrite_existing_log(self, tmp_path: Path) -> None:
        store = SessionLogStore(tmp_path)
        store.create_session_log(_start_entry())

        with pytest.raises(StateError):
            store.create_session_log(_start_entry())

    def test_append_rejects_contract_invalid_entry(self, tmp_path: Path) -> None:
        store = SessionLogStore(tmp_path)
        store.create_session_log(_start_entry())
        invalid = _entry("2026-08-17T13:00:01Z", "start-session", "started")

        with pytest.raises(StateError):
            store.append_log_entry("sess-1", invalid)

    def test_mint_workflow_instance_id_uses_crockford_base32(self, tmp_path: Path) -> None:
        minted = SessionLogStore(tmp_path).mint_workflow_instance_id("flow")

        assert re.fullmatch(r"flow-[0-9A-HJKMNP-TV-Z]{4,}", minted)

    def test_load_workflow_instance_view_orders_entries_across_logs_by_timestamp(self, tmp_path: Path) -> None:
        store = SessionLogStore(tmp_path)
        store.create_session_log(_start_entry("sess-1", "2026-08-17T13:00:00Z"))
        store.create_session_log(_start_entry("sess-2", "2026-08-17T13:00:00Z"))
        store.append_log_entry("sess-1", _resolution("2026-08-17T13:00:03Z", "flow-01AB", "beta"))
        store.append_log_entry("sess-2", _resolution("2026-08-17T13:00:01Z", "flow-01AB", "alpha", session_id="sess-2"))

        view = store.load_workflow_instance_view("flow-01AB")

        assert [entry.timestamp for entry in view.entries] == [
            "2026-08-17T13:00:01Z",
            "2026-08-17T13:00:03Z",
        ]

    def test_find_latest_open_instance_handles_none_one_multiple_and_closed(self, tmp_path: Path) -> None:
        store = SessionLogStore(tmp_path)
        assert store.find_latest_open_instance("flow", workflow_steps=("alpha",)) is None

        store.create_session_log(_start_entry("sess-1", "2026-08-17T13:00:00Z"))
        store.append_log_entry("sess-1", _resolution("2026-08-17T13:00:01Z", "flow-01AA", "alpha"))
        assert store.find_latest_open_instance("flow", workflow_steps=("alpha",)) == "flow-01AA"

        store.append_log_entry("sess-1", _postconditions("2026-08-17T13:00:02Z", "flow-01AA", "pass"))
        assert store.find_latest_open_instance("flow", workflow_steps=("alpha",)) is None

        store.append_log_entry("sess-1", _resolution("2026-08-17T13:00:03Z", "flow-01AB", "alpha"))
        store.append_log_entry("sess-1", _resolution("2026-08-17T13:00:04Z", "flow-01AC", "alpha"))
        assert store.find_latest_open_instance("flow", workflow_steps=("alpha",)) == "flow-01AC"
