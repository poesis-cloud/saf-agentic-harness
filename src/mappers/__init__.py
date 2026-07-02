"""Mappers layer — the access layer + data-mappers (model ⇄ filesystem).

`Workspace` is the shared filesystem context; each mapper maps one entity to its files:
`WorkflowMapper` ⇄ workflow.yaml, `ArtifactMapper` ⇄ the portfolio, `SchemaMapper`
⇄ JSON schemas, `LogMapper` ⇄ run logs. Mappers depend on models + the text kernel
— never on services or the CLI.
"""

from __future__ import annotations

from .artifact_mapper import ArtifactMapper, InvalidArtifactError
from .log_mapper import LogMapper
from .schema_mapper import SchemaMapper
from .workflow_mapper import WorkflowMapper
from .workspace import Workspace

__all__ = [
    "ArtifactMapper",
    "InvalidArtifactError",
    "LogMapper",
    "SchemaMapper",
    "WorkflowMapper",
    "Workspace",
]
