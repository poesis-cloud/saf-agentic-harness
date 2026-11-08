"""The condition language's variables for one evaluation (function 5, invariant 2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ConditionScope:
    """Carry the evaluated state's revision and the log-plane variables.

    Spec (function 5, invariant 2): `workspace` holds the artifact instances of
    `revision` — the workspace branch for a precondition, the step state being
    promoted for a postcondition; `workflows` and `steps` are the workflow and
    step instances this machine's logs record, `steps` oldest first; `workflow`
    and `step` are the current ones, rendered as CEL-ready plain data.
    """

    revision: str
    workflow: Mapping[str, Any]
    step: Mapping[str, Any]
    steps: tuple[Mapping[str, Any], ...]
    workflows: tuple[Mapping[str, Any], ...]


__all__ = ["ConditionScope"]
