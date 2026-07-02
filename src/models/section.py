"""A markdown section — a heading at any depth with content and nested subsections."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Section:
    """Represents a markdown section with heading level, content, and nested children.
    
    Preserves full nesting structure (not just level-2 headings) to enable
    byte-stable reconstitution of the original artifact markdown.
    
    Example:
        Section(
            level=2,
            name="Acceptance Criteria",
            body="- AC1. User can login\n- AC2. System validates email",
            children=[
                Section(level=3, name="Additional Notes", body="...", children=[])
            ]
        )
    """
    
    level: int              # Heading depth: 2 (##), 3 (###), etc.
    name: str               # Heading text (without #'s)
    body: str               # Raw content after heading (before any children)
    children: list[Section] = field(default_factory=list)  # Nested subsections
    
    def to_markdown(self, include_heading: bool = True) -> str:
        """Render section back to markdown, preserving structure.
        
        Args:
            include_heading: If False, just render body + children (used when
                           this section is already nested).
        
        Returns:
            Markdown text with all nested sections.
            
        Note: Heading and body are joined with newline. If body starts with \\n,
              that preserves the blank line between heading and content.
              When this section has no body, its heading directly abuts the
              first child heading (single newline) — a heading with no body
              text before a subheading has no blank line in the source either.
              Later siblings are still separated by a blank line.
        """
        has_body = bool(self.body.strip())
        blocks: list[str] = []
        
        if include_heading:
            heading = "#" * self.level + " " + self.name
            blocks.append(heading)
        
        if has_body:
            body = self.body
            
            if blocks:
                # Heading exists, join with single newline
                # (if body starts with \\n, that creates the blank line)
                result = "\n".join(blocks) + "\n" + body
                blocks = [result]
            else:
                blocks.append(body)
        
        # Render children (they add their own heading and spacing)
        for index, child in enumerate(self.children):
            child_md = child.to_markdown(include_heading=True)
            if index == 0 and not has_body and blocks:
                # No body separated this heading from its first child in the
                # source — abut them with a single newline, not a blank line.
                blocks[-1] = blocks[-1] + "\n" + child_md
            else:
                blocks.append(child_md)
        
        return "\n\n".join(blocks)
    
    def flatten(self) -> list[Section]:
        """Return flat list of all sections (self + all descendants, depth-first)."""
        result = [self]
        for child in self.children:
            result.extend(child.flatten())
        return result
    
    def by_name(self, name: str) -> Section | None:
        """Find first descendant section by name (case-sensitive)."""
        if self.name == name:
            return self
        for child in self.children:
            found = child.by_name(name)
            if found:
                return found
        return None
