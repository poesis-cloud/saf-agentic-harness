"""Unit tests for markdown loading mechanics."""

from __future__ import annotations

import pytest

from utils import MarkdownDocument, MarkdownLoader


class TestMarkdownDocument:
    """Verify returned markdown data shape."""

    def test_carries_frontmatter_and_body_as_frozen_data(self) -> None:
        """Spec: returned data shapes are frozen dataclasses."""
        document = MarkdownDocument(frontmatter={}, body="Body")

        assert document.frontmatter == {}
        assert document.body == "Body"
        with pytest.raises(Exception):
            document.body = "changed"  # type: ignore[misc]


class TestMarkdownLoader:
    """Verify markdown frontmatter splitting."""

    def test_loads_markdown_frontmatter_and_body(self, tmp_path) -> None:
        """Spec: MarkdownLoader splits YAML frontmatter plus body."""
        markdown_file = tmp_path / "doc.md"
        markdown_file.write_text("---\ntitle: Safe\norder: 1\n---\n# Body\nText\n", encoding="utf-8")

        document = MarkdownLoader().load_markdown(markdown_file)

        assert isinstance(document, MarkdownDocument)
        assert document.frontmatter["title"] == "Safe"
        assert document.frontmatter["order"] == 1
        assert document.body == "# Body\nText\n"
        with pytest.raises(TypeError):
            document.frontmatter["title"] = "changed"  # type: ignore[index]

    def test_loads_markdown_without_frontmatter_as_body_only(self, tmp_path) -> None:
        """Spec: MarkdownLoader returns both frontmatter and body for every file."""
        markdown_file = tmp_path / "doc.md"
        markdown_file.write_text("# Body only\n", encoding="utf-8")

        document = MarkdownLoader().load_markdown(markdown_file)

        assert dict(document.frontmatter) == {}
        assert document.body == "# Body only\n"
