"""One state-binding condition of a workflow step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class StateCondition:
    """Bind a selector to a CEL predicate over what it selects.

    Spec (`workflow.conf.schema.json`, `stateCondition`; function 5, invariant 2):
    `setSelector.setQuery` is a CEL expression over the condition language
    (`workspace`, `workflows`, `steps`, `workflow`, `step`) that yields `selected`;
    `setPredicate` is a CEL boolean over it. `workspace_kinds` is what the
    configuration load's typing check derived: the only artifact kinds either
    expression can read from `workspace`, or `None` when every kind may be read.
    """

    kind: str
    slug: str
    set_selector: Mapping[str, str]
    set_predicate: str
    workspace_kinds: frozenset[str] | None = None


__all__ = ["StateCondition"]
