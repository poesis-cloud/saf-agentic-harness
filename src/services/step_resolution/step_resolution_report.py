"""The step resolution result: the report function 3 returns and journals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import StateCondition, Step, StepCondition
from stores.session_log_store import Report


def _render_condition(condition: StepCondition | StateCondition) -> dict[str, Any]:
    """Render one of the step's flat conditions as its contract object."""
    if isinstance(condition, StepCondition):
        return {"kind": condition.kind, "slug": condition.slug, "step": condition.step}
    return {
        "kind": condition.kind,
        "slug": condition.slug,
        "setSelector": dict(condition.set_selector),
        "setPredicate": condition.set_predicate,
    }


def _render_step(step: Step) -> dict[str, Any]:
    """Render the configured step verbatim, with the contract's camelCase keys."""
    rendered: dict[str, Any] = {
        "slug": step.slug,
        "actor": step.actor,
        "artifact": step.artifact,
        "instructions": list(step.instructions),
        "capabilities": dict(step.capabilities),
    }
    if step.skills:
        rendered["skills"] = list(step.skills)
    if step.conditions:
        rendered["conditions"] = [_render_condition(condition) for condition in step.conditions]
    return rendered


@dataclass(frozen=True)
class StepResolutionReport(Report):
    """Carry function 3's outcome and, on a step resolution, the configured step.

    Spec (function 3, Out): `outcome` ± `step`, nothing more — the instance id is
    `context.workflowInstanceId`, never a second projection.
    """

    step: Step | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract report object, attaching the step when one resolved."""
        rendered = super().to_dict()
        if self.step is not None:
            rendered["step"] = _render_step(self.step)
        return rendered


__all__ = ["StepResolutionReport"]
