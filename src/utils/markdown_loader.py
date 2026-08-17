"""Load markdown files and split optional YAML frontmatter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml


@dataclass(frozen=True)
class MarkdownDocument:
    """Markdown content split into frontmatter and body."""

    frontmatter: Mapping[str, Any]
    body: str


def _freeze_data(value: Any) -> Any:
    """Convert frontmatter containers into immutable boundary data."""
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_data(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_data(item) for item in value)
    return value


class MarkdownLoader:
    """Load markdown files with optional YAML frontmatter."""

    def load_markdown(self, path: str | Path) -> MarkdownDocument:
        """Split YAML frontmatter from the markdown body."""
        content = Path(path).read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            return MarkdownDocument(frontmatter=MappingProxyType({}), body=content)

        remainder = content[4:]
        if "\n---\n" not in remainder:
            raise ValueError("Markdown frontmatter is missing its closing delimiter.")

        raw_frontmatter, body = remainder.split("\n---\n", 1)
        loaded_frontmatter = yaml.safe_load(raw_frontmatter) or {}
        if not isinstance(loaded_frontmatter, dict):
            raise ValueError("Markdown frontmatter must be a YAML mapping.")

        return MarkdownDocument(frontmatter=_freeze_data(loaded_frontmatter), body=body)


__all__ = ["MarkdownDocument", "MarkdownLoader"]
