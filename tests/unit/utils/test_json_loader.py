"""Unit tests for JSON loading mechanics."""

from __future__ import annotations

import pytest

from utils import JsonLoader


class TestJsonLoader:
    """Verify JSON files load as immutable plain data."""

    def test_loads_json_file_into_read_only_data(self, tmp_path) -> None:
        """Spec: JsonLoader loads a JSON file into plain data."""
        json_file = tmp_path / "sample.json"
        json_file.write_text('{"name": "safe", "items": [1, {"slug": "a"}]}', encoding="utf-8")

        data = JsonLoader().load_json(json_file)

        assert data["name"] == "safe"
        assert data["items"] == (1, {"slug": "a"})
        with pytest.raises(TypeError):
            data["name"] = "changed"  # type: ignore[index]
        with pytest.raises(TypeError):
            data["items"][1]["slug"] = "b"  # type: ignore[index]
