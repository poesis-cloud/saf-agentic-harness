"""An Epic / Feature / Story artifact — the abstraction of one portfolio markdown file."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from text import bool_value, list_value, parse_list_literal, parse_scalar, strip_comment

from .section import Section


@dataclass
class Artifact:
    """One portfolio unit on disk (Epic/Feature/Story).

    Holds the parsed frontmatter `fields` plus the raw `frontmatter` text, and exposes the
    accessors over both. Structured frontmatter blocks the flat `fields` dict cannot express
    (open_items, wsjf, cost, depends_on) are read from the raw text by the `*_block` / `*field`
    helpers, so behaviour matches the on-disk shape exactly.
    
    Body sections are parsed into `sections` (list of top-level Section objects) to enable
    reconstitution of the original markdown file.
    """

    kind: str
    path: Path
    fields: dict[str, Any]
    frontmatter: str
    sections: list[Section] = field(default_factory=list)
    product_slug: str | None = None
    heading: str | None = None  # File-level heading (# Title) for complete round-trip

    @property
    def artifact_id(self) -> str:
        value = self.fields.get("id")
        return str(value) if value not in (None, "") else self.path.stem

    @property
    def status(self) -> str | None:
        value = self.fields.get("status")
        return str(value) if value not in (None, "") else None

    # --- accessors over the parsed `fields` dict --------------------------------
    def field(self, key: str) -> Any:
        return self.fields.get(key)

    def bool_field(self, key: str) -> bool:
        return bool_value(self.fields.get(key))

    def list_field(self, key: str) -> list[str]:
        return list_value(self.fields.get(key))

    # --- accessors over the raw frontmatter text --------------------------------
    def has_blocking_open_items(self) -> bool:
        frontmatter_text = self.frontmatter
        open_items_match = re.search(r"^open_items:\s*(.*)$", frontmatter_text, re.MULTILINE)
        if not open_items_match:
            return False
        inline = open_items_match.group(1).strip()
        if inline == "[]":
            return False
        open_items_start = open_items_match.start()
        following = frontmatter_text[open_items_start:]
        next_top_level = re.search(r"\n[A-Za-z_][\w-]*:\s*", following[1:])
        block = following if next_top_level is None else following[: next_top_level.start() + 1]
        compact = " ".join(block.split())
        if re.search(r"blocking:\s*true", compact) and re.search(r"status:\s*open", compact):
            return True
        if re.search(r"\bblocking\s*[:=]\s*true\b", compact) and re.search(r"\bstatus\s*[:=]\s*open\b", compact):
            return True
        return False

    def field_value(self, key: str) -> str | None:
        match = re.search(rf"^{re.escape(key)}:\s*(.*)$", self.frontmatter, re.MULTILINE)
        if match is None:
            return None
        value = strip_comment(match.group(1)).strip()
        if value in {"", "null", "~", "[]"}:
            return None
        return value

    def block(self, key: str) -> list[str]:
        lines = self.frontmatter.splitlines()
        for index, line in enumerate(lines):
            if re.match(rf"^{re.escape(key)}:\s*", line):
                block = [line]
                for following in lines[index + 1 :]:
                    if following and not following.startswith(" ") and re.match(r"^[A-Za-z_][\w-]*:\s*", following):
                        break
                    block.append(following)
                return block
        return []

    @staticmethod
    def block_list(block: list[str], key: str) -> list[str]:
        values: list[str] = []
        for index, line in enumerate(block):
            match = re.match(rf"^\s*{re.escape(key)}:\s*(.*)$", line)
            if match is None:
                continue
            inline_values = parse_list_literal(match.group(1))
            if inline_values:
                values.extend(inline_values)
                continue
            current_indent = len(line) - len(line.lstrip())
            for following in block[index + 1 :]:
                if not following.strip():
                    continue
                indent = len(following) - len(following.lstrip())
                if indent <= current_indent:
                    break
                item = following.strip()
                if item.startswith("- "):
                    values.append(str(parse_scalar(item[2:].strip())))
        return [str(value) for value in values if str(value)]

    def dependency_ids(self) -> list[str]:
        dependency_ids: list[str] = []
        dependency_ids.extend(self.block_list(self.block("depends_on"), "depends_on"))
        dependency_ids.extend(self.block_list(self.block("work_item_relations"), "depends_on"))
        return sorted(set(dependency_ids))

    # --- reconstitution + serialization ----------------------------------------
    def to_markdown(self) -> str:
        """Render artifact back to markdown — frontmatter + heading + all sections.
        
        Produces byte-stable output (identical to original file, up to whitespace
        normalization). This makes Artifact a true aggregate root.
        """
        blocks: list[str] = []
        
        # Frontmatter block (consecutive lines)
        if self.frontmatter.strip():
            blocks.append("---")
            blocks.append(self.frontmatter.rstrip())
            blocks.append("---")
            frontmatter_block = "\n".join(blocks)
            blocks = [frontmatter_block]
        
        # File-level heading (if present)
        if self.heading:
            blocks.append(self.heading)
        
        # Body sections
        for section in self.sections:
            blocks.append(section.to_markdown(include_heading=True))
        
        return "\n\n".join(blocks) + "\n" if blocks else ""
    
    def section_by_name(self, name: str) -> Section | None:
        """Find a top-level section by name."""
        for section in self.sections:
            if section.name == name:
                return section
        return None
    
    def all_sections_flat(self) -> list[Section]:
        """Return flat list of all sections (top-level + all descendants, depth-first)."""
        result: list[Section] = []
        for section in self.sections:
            result.extend(section.flatten())
        return result
