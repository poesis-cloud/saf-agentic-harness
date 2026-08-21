"""The typed view over `conf/workspace.conf.yaml`."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from errors import ConfigurationError

_LOGS_SEGMENT = "logs"
_PLACEHOLDER_PATTERN = re.compile(r"<([a-z0-9-]+)>")
_PLACEHOLDER_VALUE_PATTERN = "([^/]+)"

# Segment-language tokens: one arbitrary character, and zero or more of them. A
# `<name>` placeholder is `[^/]+`, so it tokenizes to _ANY followed by _STAR.
_ANY = object()
_STAR = object()


def _tokenize_segment(slug: str) -> tuple[object, ...]:
    """Expand one node slug into single-character tokens over the segment alphabet."""
    tokens: list[object] = []
    cursor = 0
    for placeholder in _PLACEHOLDER_PATTERN.finditer(slug):
        tokens.extend(slug[cursor : placeholder.start()])
        tokens.extend((_ANY, _STAR))
        cursor = placeholder.end()
    tokens.extend(slug[cursor:])
    return tuple(tokens)


def _accepts_empty(tokens: Sequence[object], index: int) -> bool:
    """Tell whether a token suffix can match the empty string."""
    return all(token is _STAR for token in tokens[index:])


def _tokens_agree(left: object, right: object) -> bool:
    """Tell whether two tokens can consume one and the same character."""
    if left is _ANY or left is _STAR or right is _ANY or right is _STAR:
        return True
    return left == right


def _segments_overlap(left: Sequence[object], right: Sequence[object]) -> bool:
    """Decide whether two token sequences accept a common segment string.

    Product-automaton reachability over the two segment languages: exact, because the
    only character classes are single literals and "any character but `/`", so two
    tokens either agree on a concrete character or one of them accepts every character.
    """
    start = (0, 0)
    seen = {start}
    pending = [start]
    while pending:
        index, other = pending.pop()
        if _accepts_empty(left, index) and _accepts_empty(right, other):
            return True

        successors: list[tuple[int, int]] = []
        if index < len(left) and left[index] is _STAR:
            successors.append((index + 1, other))
        if other < len(right) and right[other] is _STAR:
            successors.append((index, other + 1))
        if (
            index < len(left)
            and other < len(right)
            and _tokens_agree(left[index], right[other])
        ):
            successors.append(
                (
                    index if left[index] is _STAR else index + 1,
                    other if right[other] is _STAR else other + 1,
                )
            )

        for successor in successors:
            if successor not in seen:
                seen.add(successor)
                pending.append(successor)
    return False


def paths_can_collide(left: Sequence[str], right: Sequence[str]) -> bool:
    """Tell whether two root-to-leaf slug paths can name one and the same path.

    Sound over-approximation: `<name>` placeholders are read as independent wildcards,
    so a repeated name's binding equality — which only ever REMOVES strings from a
    pattern's language — is not enforced here. The answer therefore never misses a real
    collision, and only over-reports when every candidate collision is ruled out purely
    by that equality (e.g. `<x>/<x>.md` against `<y>/<y>-v2.md`).
    """
    if len(left) != len(right):
        return False
    return all(
        _segments_overlap(_tokenize_segment(one), _tokenize_segment(other))
        for one, other in zip(left, right)
    )


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
    resolved from the write path. A layout in which one path resolves to two kinds is
    refused at configuration load, so resolution here answers one kind or none.
    Invariant 6: a write targeting the workspace logs path is denied always.
    """

    nodes: tuple[ArtifactNode | FolderNode, ...]

    def resolve_resource(self, path: str | Path) -> str:
        """Resolve a workspace-relative path to the artifact slug it is bound to."""
        segments = _strip_fragment(Path(path))
        matched = tuple(
            sorted({node.artifact for node in self._match_artifact_nodes(self.nodes, segments, {})})
        )

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
                f"{', '.join(matched)}: the layout was not refused at load.",
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


__all__ = ["WorkspaceLayout", "paths_can_collide"]
