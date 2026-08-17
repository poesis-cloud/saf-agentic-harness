"""An artifact (file) node of the workspace layout tree."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactNode:
    """Bind one path segment to an artifact schema slug and a template slug.

    Spec (`workspace.conf.schema.json`): an artifact node is a leaf — artifacts are files,
    not folders — and its `slug` is its OWN path segment, possibly embedding `<name>`
    variable placeholders.
    """

    slug: str
    description: str
    cardinality: str
    artifact: str
    template: str


__all__ = ["ArtifactNode"]
