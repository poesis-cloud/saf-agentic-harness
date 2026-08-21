"""Unit tests for the session log store package."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from errors import InquiryError, StateError
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
        """Spec (Logging): the envelope every entry carries — the function, the session ids,
        and the workflow instance — rendered under the log-entry contract's own key names."""
        context = Context("start-session", "sess-1", None, "flow-01AB")

        assert context.to_dict() == {
            "function": "start-session",
            "sessionId": "sess-1",
            "parentSessionId": None,
            "workflowInstanceId": "flow-01AB",
        }


class TestError:
    def test_renders_optional_retryable(self) -> None:
        """Spec (Outcomes): `error.code` and `retryable` are structured, normative fields —
        rendered alongside the advisory `message`."""
        assert Error("bad", "Bad", True).to_dict() == {
            "code": "bad",
            "message": "Bad",
            "retryable": True,
        }


class TestOutcome:
    def test_renders_error_when_present(self) -> None:
        """Spec (Outcomes): a non-success outcome carries its `error` object; `error.code`
        is the normative test surface."""
        outcome = Outcome("state-error", Error("bad", "Bad", False))

        assert outcome.to_dict()["error"]["code"] == "bad"


class TestReport:
    def test_renders_context_outcome_and_payload(self) -> None:
        """Spec (Classes, reports): a report is the envelope plus its function-specific
        payload, which renders flat beside `context` and `outcome`."""
        report = _report("end-session", "ended", payload={"extra": 1})

        assert report.to_dict()["extra"] == 1


class TestLogEntry:
    def test_round_trips_from_contract_dict(self) -> None:
        """Spec (Logging): entries are the persisted contract form — a rendered entry parses
        back into an equal entry, which is what makes byte-stable replay possible."""
        entry = _start_entry()

        assert LogEntry.from_dict(entry.to_dict()) == entry


class TestLog:
    def test_exposes_session_and_entries(self) -> None:
        """Spec (Logging): a log is one session's ordered entries, keyed by that session —
        single-writer, append-only."""
        entry = _start_entry()
        log = Log("sess-1", (entry,))

        assert log.session_id == "sess-1"
        assert log.entries == (entry,)


class TestWorkflowInstanceView:
    def test_queries_executed_steps_latest_outcomes_and_unresolved_resolution(self) -> None:
        """Spec (function 3, invariant 1): a step counts as executed when its LATEST
        journaled function-10 outcome passes — latest wins, so a re-run that fails drops the
        step back out; and the actor's resolution with no later outcome is the in-flight one."""
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
        """Spec (Logging): the log is the persisted record — what is loaded re-renders to the
        exact bytes on disk, one JSON object per line, so replay is byte-stable."""
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
        """Spec (Logging): logs are append-only — creation never truncates an existing log,
        so no registration can silently erase a session's history."""
        store = SessionLogStore(tmp_path)
        store.create_session_log(_start_entry())

        with pytest.raises(StateError):
            store.create_session_log(_start_entry())

    def test_append_rejects_contract_invalid_entry(self, tmp_path: Path) -> None:
        """Spec (Internal validation): an entry that fails the log-entry contract never
        reaches the log — the journal cannot hold a record no contract admits."""
        store = SessionLogStore(tmp_path)
        store.create_session_log(_start_entry())
        invalid = _entry("2026-08-17T13:00:01Z", "start-session", "started")

        with pytest.raises(StateError):
            store.append_log_entry("sess-1", invalid)

    def test_mint_workflow_instance_id_uses_crockford_base32(self, tmp_path: Path) -> None:
        """Spec (function 3, invariant 8): instance ids are minted by the harness as
        `<workflowSlug>-<Crockford base32>` — agents never pass or mint them."""
        minted = SessionLogStore(tmp_path).mint_workflow_instance_id("flow")

        assert re.fullmatch(r"flow-[0-9A-HJKMNP-TV-Z]{4,}", minted)

    def test_load_workflow_instance_view_orders_entries_across_logs_by_timestamp(self, tmp_path: Path) -> None:
        """Spec (function 3, invariant 1 / the instance view): the view is assembled ACROSS
        session logs in timestamp order — one instance spans the orchestrator's log and every
        step session's log, and is derived, never persisted."""
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
        """Spec (function 3, invariant 8): instance correlation is deduced, latest-open-wins
        — no instance, one open instance, an instance closed by every step passing, and two
        open instances where the later one is the one continued. No register closes anything."""
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


class TestSessionLogStoreSlugConstraint:
    """The store owns the safe-slug constraint because the store owns the filename."""

    @pytest.mark.parametrize(
        "session_id",
        ["../escape", "/tmp/escape", "nested/id", "..", "", "Sess-1", "sess_1"],
    )
    def test_load_refuses_every_session_id_the_slug_form_rejects(
        self, tmp_path: Path, session_id: str
    ) -> None:
        """Spec (Logging, Sanitization): `sessionId` becomes a log filename, so only the safe
        slug form `[a-z0-9-]+` may name one; anything else is a path-traversal vector.
        Spec (Outcomes, rule 1): a non-slug `sessionId` is `invalid-inquiry`."""
        store = SessionLogStore(tmp_path)

        with pytest.raises(InquiryError) as raised:
            store.load_session_log(session_id)

        assert raised.value.code == "invalid-inquiry"

    def test_load_refuses_to_read_a_log_outside_the_workspace_logs_directory(
        self, tmp_path: Path
    ) -> None:
        """Spec (Logging): logs live under `<workspace>/logs/` — a crafted `sessionId` must not
        let a caller read a file the store does not own."""
        outside = tmp_path / "escape.log.jsonl"
        outside.write_text(
            json.dumps(_start_entry().to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        store = SessionLogStore(tmp_path)

        with pytest.raises(InquiryError):
            store.load_session_log("../escape")

    def test_append_refuses_to_write_to_a_log_outside_the_workspace_logs_directory(
        self, tmp_path: Path
    ) -> None:
        """Spec (Logging): the journal is append-only INSIDE `<workspace>/logs/` — a crafted
        `sessionId` must not append a line to a file outside it."""
        outside = tmp_path / "escape.log.jsonl"
        outside.write_text("", encoding="utf-8")
        store = SessionLogStore(tmp_path)

        with pytest.raises(InquiryError):
            store.append_log_entry("../escape", _start_entry())

        assert outside.read_text(encoding="utf-8") == ""

    def test_create_refuses_the_unsafe_slug_before_it_validates_the_entry(
        self, tmp_path: Path
    ) -> None:
        """Spec (Logging, Sanitization): the store cannot name a file for an unsafe id, so the
        filename constraint precedes any entry-shape concern — the refusal is `invalid-inquiry`,
        not the `invalid-log-entry` the contract check would otherwise report."""
        store = SessionLogStore(tmp_path)

        with pytest.raises(InquiryError) as raised:
            store.create_session_log(_start_entry("../escape"))

        assert raised.value.code == "invalid-inquiry"
        assert not (tmp_path / "escape.log.jsonl").exists()

