"""A folder node of the workspace layout tree."""

from __future__ import annotations

from dataclasses import dataclass

from config.artifact_node import ArtifactNode


@dataclass(frozen=True)
class FolderNode:
    """Contain child nodes without binding any artifact schema.

    Spec (`workspace.conf.schema.json`): a folder node is a container of `children` with
    no schema, template, or required cardinality of its own.
    """

    slug: str
    description: str
    children: tuple["ArtifactNode | FolderNode", ...]
    cardinality: str | None = None


__all__ = ["FolderNode"]
