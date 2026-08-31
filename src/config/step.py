"""One workflow step: one actor's turn over one artifact."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from config.state_condition import StateCondition
from config.step_condition import StepCondition


@dataclass(frozen=True)
class Step:
    """Carry one step's actor, artifact, refs, obligations, and capability demand.

    Spec (`workflow.conf.schema.json`, `step`): exactly one `actor` and exactly one
    `artifact` per step — 1 step = 1 agent = 1 session = 1 artifact — with ONE flat
    `conditions` list of step-binding and state-binding entries.
    """

    slug: str
    actor: str
    artifact: str
    instructions: tuple[str, ...]
    capabilities: Mapping[str, float]
    skills: tuple[str, ...] = ()
    conditions: tuple[StepCondition | StateCondition, ...] = ()
    description: str | None = None


__all__ = ["Step"]
