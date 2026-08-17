"""One state-binding condition of a workflow step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class StateCondition:
    """Bind an artifact-backed selector to a CEL predicate over the selected set.

    Spec (`workflow.conf.schema.json`, `stateCondition`): `setSelector.setQuery` is a CEL
    query referencing artifacts by their schema slug to yield `selected`; `setPredicate` is
    a CEL boolean over that set.
    """

    kind: str
    slug: str
    set_selector: Mapping[str, str]
    set_predicate: str


__all__ = ["StateCondition"]
