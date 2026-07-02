"""An artifact JSON schema (`*.artifact.schema.json`) with its x-artifact metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .section_spec import SectionSpec


@dataclass
class ArtifactSchema:
    path: Path
    schema_id: str
    schema: dict[str, Any]
    artifact_kind: str
    artifact_type: str | None
    template_path: Path
    path_patterns: list[str]
    render_only: bool
    sections_spec: SectionSpec | None = None
