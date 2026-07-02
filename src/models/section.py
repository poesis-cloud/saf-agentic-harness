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
        """
        lines: list[str] = []
        
        if include_heading:
            heading = "#" * self.level + " " + self.name
            lines.append(heading)
        
        if self.body.strip():
            lines.append(self.body)
        
        for child in self.children:
            # Children render with their own headings
            lines.append(child.to_markdown(include_heading=True))
        
        return "\n\n".join(lines)
    
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
