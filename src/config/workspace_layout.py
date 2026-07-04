"""WorkspaceLayout — the typed view over conf/workspace.conf.yaml (the workspace blueprint).

A recursive tree of nodes binding workspace paths (with <slug> placeholders) to artifact schemas,
templates, and cardinalities. Also derives the singleton path->kind map the authorization plane
uses to classify well-known single-instance files.
"""

from __future__ import annotations

import re
from typing import Any, Iterator


class WorkspaceLayout:
    """Typed accessors over the validated workspace layout document."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._nodes: list[dict[str, Any]] = list(data.get("nodes") or [])

    def nodes(self) -> list[dict[str, Any]]:
        return self._nodes

    def walk(self) -> Iterator[dict[str, Any]]:
        """Depth-first over every node in the tree."""
        stack = list(reversed(self._nodes))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(node.get("children") or []))

    @staticmethod
    def _kind_of(schema_name: str) -> str:
        suffix = ".artifact.schema.json"
        return schema_name[: -len(suffix)] if schema_name.endswith(suffix) else schema_name

    @staticmethod
    def _glob_of(path: str) -> str:
        """A node path with <slug> placeholders as an fnmatch glob (each placeholder -> *)."""
        return re.sub(r"<[^>]+>", "*", path)

    def singleton_path_kind(self) -> dict[str, str]:
        """fnmatch pattern -> artifact kind, for every schema-bound node with cardinality '1'.
        The authorization funnel uses it to classify writes to well-known singleton files."""
        mapping: dict[str, str] = {}
        for node in self.walk():
            schema = node.get("schema")
            if schema and str(node.get("cardinality")) == "1":
                mapping[self._glob_of(str(node["path"]))] = self._kind_of(str(schema))
        return mapping

    def schema_bindings(self) -> dict[str, str]:
        """fnmatch pattern -> artifact schema filename, for every schema-bound node."""
        return {
            self._glob_of(str(node["path"])): str(node["schema"])
            for node in self.walk()
            if node.get("schema")
        }


__all__ = ["WorkspaceLayout"]
