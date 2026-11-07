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


def test_an_ignored_return_is_consumed_exactly_once(tmp_path) -> None:
    """Adapter spec H6: each sub-agent whose registration was refused (or never happened)
    accounts for exactly ONE dispatch return of its dispatcher that is not evaluated."""
    tracker = SessionTracker(tmp_path)
    tracker.mark_ignored_return("host", "worker-b")
    tracker.mark_ignored_return("host", None)

    assert [tracker.consume_ignored_return("host") for _ in range(3)] == [True, True, False]
    assert tracker.consume_ignored_return("other-host") is False


def test_closing_a_session_forgets_its_pending_ignored_returns(tmp_path) -> None:
    """A pending ignored return belongs to its dispatcher's session: closing (or a new
    turn on the same host session) drops it, so it never swallows a later turn's return."""
    tracker = SessionTracker(tmp_path)
    tracker.mark_ignored_return("host", "worker-b")
    tracker.delete("host")
    assert tracker.consume_ignored_return("host") is False

    tracker.mark_ignored_return("host", "worker-c")
    tracker.clear_ignored_returns("host")
    assert tracker.consume_ignored_return("host") is False
