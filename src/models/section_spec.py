"""Section schema specification — expected body structure for artifact kinds."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class SectionSpec:
    """Defines the expected markdown section structure for an artifact kind.
    
    Example:
        SectionSpec(
            required=["Acceptance Criteria"],
            optional=["Preconditions", "Implementation Notes"],
            max_depth=2,
            allow_unknown=False,
            patterns={"Acceptance Criteria": "bullet_list"}
        )
    """

    required: list[str]
    optional: list[str]
    max_depth: int = 2
    allow_unknown: bool = False
    patterns: dict[str, str] | None = None  # section_name → pattern_name

    @property
    def all_sections(self) -> set[str]:
        return set(self.required) | set(self.optional)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SectionSpec:
        """Load from JSON x-sections metadata."""
        return cls(
            required=data.get("required", []),
            optional=data.get("optional", []),
            max_depth=data.get("maxDepth", 2),
            allow_unknown=data.get("allowUnknown", False),
            patterns=data.get("patterns"),
        )
