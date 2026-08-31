"""Unit tests for unique filename-stem indexing."""

from __future__ import annotations

from pathlib import Path

import pytest

from errors import ConfigurationError
from utils.named_file import index_unique_stems


class TestIndexUniqueStems:
    """A slug is the unique filename stem under a canonical directory."""

    def test_indexes_nested_files_by_filename_stem(self, tmp_path: Path) -> None:
        nested = tmp_path / "product-manager"
        nested.mkdir()
        path = nested / "draft.instructions.md"
        path.write_text("# draft\n", encoding="utf-8")

        index = index_unique_stems(tmp_path, ".instructions.md")

        assert index == {"draft": path}

    def test_refuses_the_same_stem_in_two_folders(self, tmp_path: Path) -> None:
        first = tmp_path / "a" / "draft.instructions.md"
        second = tmp_path / "b" / "draft.instructions.md"
        first.parent.mkdir()
        second.parent.mkdir()
        first.write_text("one\n", encoding="utf-8")
        second.write_text("two\n", encoding="utf-8")

        with pytest.raises(ConfigurationError) as failure:
            index_unique_stems(tmp_path, ".instructions.md")

        assert failure.value.code == "duplicate-filename-stem"
