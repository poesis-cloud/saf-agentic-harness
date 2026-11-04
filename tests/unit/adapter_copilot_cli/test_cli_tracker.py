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
