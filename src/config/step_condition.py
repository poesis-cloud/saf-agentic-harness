"""One step-binding condition of a workflow step."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepCondition:
    """Bind another step by slug.

    Spec (`workflow.conf.schema.json`, `stepCondition`): `kind` determines the direction of
    `step` — the predecessor that must be journaled executed (`precondition`, the hard
    structural gate), or the successor this step unblocks (`postcondition`, advisory).
    """

    kind: str
    slug: str
    step: str


__all__ = ["StepCondition"]
