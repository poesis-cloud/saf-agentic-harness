"""The typed view over `conf/workspace.conf.yaml`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from errors import ConfigurationError

_LOGS_SEGMENT = "logs"
_PLACEHOLDER_PATTERN = re.compile(r"<([a-z0-9-]+)>")
_PLACEHOLDER_VALUE_PATTERN = "([^/]+)"


def _compile_segment_pattern(slug: str) -> tuple[re.Pattern[str], tuple[str, ...]]:
    """Compile one node slug into a regex plus the parallel placeholder-name list.

    Positional groups with a name list, not named groups: `re` forbids duplicate group
    names while `nodeSlug` allows the same variable name to recur within one slug.
    """
    parts: list[str] = []
    names: list[str] = []
    cursor = 0
    for placeholder in _PLACEHOLDER_PATTERN.finditer(slug):
        parts.append(re.escape(slug[cursor : placeholder.start()]))
        parts.append(_PLACEHOLDER_VALUE_PATTERN)
        names.append(placeholder.group(1))
        cursor = placeholder.end()
    parts.append(re.escape(slug[cursor:]))
    return re.compile("".join(parts)), tuple(names)


def _bind_segment(
    slug: str,
    segment: str,
    bindings: Mapping[str, str],
) -> dict[str, str] | None:
    """Match one path segment against one node slug, extending the shared bindings."""
    pattern, names = _compile_segment_pattern(slug)
    matched = pattern.fullmatch(segment)
    if matched is None:
        return None

    extended = dict(bindings)
    for name, value in zip(names, matched.groups()):
        if extended.setdefault(name, value) != value:
            return None
    return extended


def _strip_fragment(path: Path) -> tuple[str, ...]:
    """Drop any `#property` suffix: authorization is whole-resource (function 8, invariant 3)."""
    parts = path.parts
    if not parts:
        return parts
    return parts[:-1] + (parts[-1].partition("#")[0],)


@dataclass(frozen=True)
class WorkspaceLayout:
    """Resolve workspace paths to artifact kinds and guard the logs plane.

    Spec (function 8, invariant 2): the resource is the artifact's schema identity
    resolved from the write path, disambiguated by the artifact's type when several
    path patterns match. Invariant 6: a write targeting the workspace logs path is
    denied always.
    """

    nodes: tuple[ArtifactNode | FolderNode, ...]

    def resolve_resource(self, path: str | Path, artifact_type: str | None) -> str:
        """Resolve a workspace-relative path to the artifact slug it is bound to."""
        segments = _strip_fragment(Path(path))
        matched = tuple(
            sorted({node.artifact for node in self._match_artifact_nodes(self.nodes, segments, {})})
        )

        if artifact_type is not None:
            if artifact_type not in matched:
                raise ConfigurationError(
                    "unresolvable-resource",
                    f"Path '{path}' is unresolvable against the workspace layout "
                    f"for artifact type '{artifact_type}'.",
                    False,
                )
            return artifact_type

        if not matched:
            raise ConfigurationError(
                "unresolvable-resource",
                f"Path '{path}' is unresolvable against the workspace layout.",
                False,
            )
        if len(matched) > 1:
            raise ConfigurationError(
                "ambiguous-resource",
                f"Path '{path}' is ambiguous across artifact types "
                f"{', '.join(matched)}: an artifact type is required to disambiguate.",
                False,
            )
        return matched[0]

    def is_logs_path(self, path: str | Path) -> bool:
        """Tell whether a workspace-relative path targets the harness logs plane."""
        parts = Path(path).parts
        return bool(parts) and parts[0] == _LOGS_SEGMENT

    def _match_artifact_nodes(
        self,
        nodes: tuple[ArtifactNode | FolderNode, ...],
        segments: tuple[str, ...],
        bindings: Mapping[str, str],
    ) -> tuple[ArtifactNode, ...]:
        """Walk the tree, binding `<name>` placeholders consistently down each branch."""
        if not segments:
            return ()

        head, tail = segments[0], segments[1:]
        matched: list[ArtifactNode] = []
        for node in nodes:
            extended = _bind_segment(node.slug, head, bindings)
            if extended is None:
                continue
            if isinstance(node, FolderNode):
                matched.extend(self._match_artifact_nodes(node.children, tail, extended))
            elif not tail:
                matched.append(node)
        return tuple(matched)


__all__ = ["WorkspaceLayout"]
