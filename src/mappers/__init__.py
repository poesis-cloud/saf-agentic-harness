"""Mappers layer — workspace data access only (model ⇄ the workspace filesystem).

`Workspace` is the shared filesystem context; each mapper maps one workspace entity to its
files: `ArtifactMapper` ⇄ the workspace units, `LogMapper` ⇄ run logs. Configuration
access (workflows, schemas, ACL, model catalog, layout) lives in the `config` package —
mappers hold no config concerns. Mappers depend on models + the text kernel — never on
services or the CLI.
"""

from __future__ import annotations

from .artifact_mapper import ArtifactMapper, InvalidArtifactError
from .log_mapper import LogMapper
from .workspace import Workspace

__all__ = [
    "ArtifactMapper",
    "InvalidArtifactError",
    "LogMapper",
    "Workspace",
]
