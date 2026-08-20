"""One condition's outcome — the audit record keyed by the condition slug."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from config import StateCondition, StepCondition


def _render_condition(condition: StepCondition | StateCondition) -> dict[str, Any]:
    """Render one condition verbatim in its workflow-configuration contract form."""
    if isinstance(condition, StepCondition):
        return {
            "kind": condition.kind,
            "slug": condition.slug,
            "step": condition.step,
        }
    return {
        "kind": condition.kind,
        "slug": condition.slug,
        "setSelector": dict(condition.set_selector),
        "setPredicate": condition.set_predicate,
    }


@dataclass(frozen=True)
class ConditionCheck:
    """Carry one checked condition, its outcome, and its failure message.

    Spec (function 5, invariant 3): condition slugs are the audit handle — every
    check logs the FULL condition object with its outcome under that slug.
    """

    condition: StepCondition | StateCondition
    outcome: str
    failure_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract `conditionChecks[]` entry for this check."""
        rendered: dict[str, Any] = {
            "condition": _render_condition(self.condition),
            "outcome": self.outcome,
        }
        if self.failure_message is not None:
            rendered["failureMessage"] = self.failure_message
        return rendered


__all__ = ["ConditionCheck"]
