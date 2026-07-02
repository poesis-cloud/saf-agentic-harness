"""Persistence layer — the access layer + data-mappers (model ⇄ filesystem).

`Workspace` is the shared filesystem context; each repository maps one entity to its files:
`WorkflowRepository` ⇄ workflow.yaml, `ArtifactRepository` ⇄ the portfolio, `SchemaRepository`
⇄ JSON schemas, `LogRepository` ⇄ run logs. Repositories depend on models + the text kernel
— never on services or the CLI.
"""

from __future__ import annotations

from .artifact_repository import ArtifactRepository, InvalidArtifactError
from .artifact_validator import ArtifactValidator
from .log_repository import LogRepository
from .schema_repository import SchemaRepository
from .workflow_repository import WorkflowRepository
from .workspace import Workspace

__all__ = [
    "ArtifactRepository",
    "ArtifactValidator",
    "InvalidArtifactError",
    "LogRepository",
    "SchemaRepository",
    "WorkflowRepository",
    "Workspace",
]
