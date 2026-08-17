"""Unit tests for JSONL storage mechanics."""

from __future__ import annotations

import pytest

from utils import JsonlStore


class TestJsonlStore:
    """Verify append-only JSONL persistence."""

    def test_appends_and_loads_jsonl_entries(self, tmp_path) -> None:
        """Spec: JsonlStore appends one JSON line and loads entries as a tuple."""
        log_file = tmp_path / "session.log.jsonl"
        store = JsonlStore()
        first_entry = {"timestamp": "2026-07-08T14:32:07Z", "report": {"outcome": "started"}}
        second_entry = {"timestamp": "2026-07-08T14:33:07Z", "report": {"outcome": "ended"}}

        store.append_entry(log_file, first_entry)
        store.append_entry(log_file, second_entry)
        entries = store.load_entries(log_file)

        assert len(entries) == 2
        assert entries[0]["timestamp"] == first_entry["timestamp"]
        assert entries[1]["report"]["outcome"] == "ended"
        assert log_file.read_text(encoding="utf-8").count("\n") == 2
        with pytest.raises(TypeError):
            entries[0]["timestamp"] = "changed"  # type: ignore[index]

    def test_loads_empty_jsonl_file_as_empty_tuple(self, tmp_path) -> None:
        """Spec: load_entries returns tuple data across the boundary."""
        log_file = tmp_path / "empty.log.jsonl"
        log_file.write_text("", encoding="utf-8")

        assert JsonlStore().load_entries(log_file) == ()
