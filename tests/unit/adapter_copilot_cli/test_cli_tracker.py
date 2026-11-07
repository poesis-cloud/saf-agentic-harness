"""Session records: per host id, atomic, descendants, corrupt → fail closed."""

from __future__ import annotations

from pathlib import Path

import pytest

from copilot_cli_adapter.tracker import (
    STEP_ORIGIN,
    TURN_ORIGIN,
    SessionRecord,
    SessionTracker,
    TrackerError,
)


def test_records_round_trip_and_list_descendants(tmp_path: Path) -> None:
    tracker = SessionTracker(tmp_path)
    tracker.write("host", SessionRecord("host-t1", "rte", TURN_ORIGIN))
    tracker.write("step", SessionRecord("step", "sa", STEP_ORIGIN, "host-t1", "host"))
    tracker.write("nested", SessionRecord("nested", "dev", STEP_ORIGIN, "step", "step"))
    assert tracker.read("host") == SessionRecord("host-t1", "rte", TURN_ORIGIN)
    assert set(tracker.descendants("host")) == {"step", "nested"}
    tracker.delete("step")
    assert tracker.read("step") is None


def test_a_corrupt_record_raises_instead_of_reading_as_ungoverned(tmp_path: Path) -> None:
    tracker = SessionTracker(tmp_path)
    tracker.write("host", SessionRecord("host-t1", "rte", TURN_ORIGIN))
    (tmp_path / "sessions" / "host.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(TrackerError):
        tracker.read("host")
    assert tracker.descendants("other") == ()


@pytest.mark.parametrize("unsafe", ["", "../x", "a/b", "a\\b", ".hidden"])
def test_unsafe_ids_are_refused(tmp_path: Path, unsafe: str) -> None:
    with pytest.raises(ValueError):
        SessionTracker(tmp_path).read(unsafe)


def test_an_ignored_return_is_consumable_only_once_its_sub_agent_stopped(tmp_path) -> None:
    """Adapter spec H6: each sub-agent whose registration was refused (or never happened)
    accounts for exactly ONE dispatcher return that is not evaluated — and only once that
    very sub-agent has stopped, so a bound worker returning first is still evaluated."""
    tracker = SessionTracker(tmp_path)
    tracker.mark_ignored_return("host", "worker-b")
    tracker.mark_ignored_return("host", "worker-c")

    assert tracker.consume_ignored_return("host") is False
    assert tracker.arm_ignored_return("host", "worker-a") is False
    assert tracker.arm_ignored_return("host", "worker-b") is True
    assert tracker.arm_ignored_return("host", "worker-b") is False
    assert [tracker.consume_ignored_return("host") for _ in range(2)] == [True, False]
    assert tracker.arm_ignored_return("host", "worker-c") is True
    assert tracker.consume_ignored_return("host") is True
    assert tracker.consume_ignored_return("other-host") is False


def test_closing_a_session_forgets_its_pending_ignored_returns(tmp_path) -> None:
    """A pending ignored return belongs to its dispatcher's session: closing (or a new
    turn on the same host session) drops it, so it never swallows a later turn's return."""
    tracker = SessionTracker(tmp_path)
    tracker.mark_ignored_return("host", "worker-b")
    tracker.arm_ignored_return("host", "worker-b")
    tracker.delete("host")
    assert tracker.consume_ignored_return("host") is False

    tracker.mark_ignored_return("host", "worker-c")
    tracker.arm_ignored_return("host", "worker-c")
    tracker.clear_ignored_returns("host")
    assert tracker.consume_ignored_return("host") is False


def test_a_return_that_loses_every_unlink_race_re_lists_the_armed_markers(
    tmp_path, monkeypatch
) -> None:
    """Adapter spec H6: a return snapshots marker B, a concurrent return consumes B while
    sub-agent C stops and arms its own marker. The losing return must still consume C's —
    otherwise an ignored return would evaluate the step while the bound worker runs."""
    tracker = SessionTracker(tmp_path)
    for worker in ("worker-b", "worker-c"):
        tracker.mark_ignored_return("host", worker)
    tracker.arm_ignored_return("host", "worker-b")
    original_unlink = Path.unlink
    raced: list[str] = []

    def racing_unlink(self: Path, missing_ok: bool = False) -> None:
        if not raced and self.name == "worker-b":
            raced.append(self.name)
            original_unlink(self)
            tracker.arm_ignored_return("host", "worker-c")
            raise FileNotFoundError(self)
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", racing_unlink)

    assert tracker.consume_ignored_return("host") is True
    assert raced == ["worker-b"]
    assert tracker.consume_ignored_return("host") is False
