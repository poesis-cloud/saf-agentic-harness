"""One structurant step — a single actor's turn, with its flat conditions list."""

from __future__ import annotations

from typing import Any

from .condition import Condition


class Step:
    """A workflow step: one `actor`, one `kind`, and a flat `conditions` list.

    Exposes the structural wiring (`after_ids`), step-level guidance (`instructions`,
    `prompts`), and dispatch metadata (`skills`, `output`) the checkers read, without
    leaking the raw mapping shape.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def raw_id(self) -> Any:
        return self._data.get("id")

    @property
    def id(self) -> str:
        return str(self._data.get("id"))

    @property
    def actor(self) -> Any:
        return self._data.get("actor")

    @property
    def skills(self) -> list[str]:
        """The skill ids the dispatched agent loads for this step (per-step, not per-workflow)."""
        raw = self._data.get("skills")
        return [str(s) for s in raw] if isinstance(raw, list) else []

    @property
    def capabilities(self) -> dict[str, float]:
        """The step's weighted capability demand: capability tag -> weight (0-10). Static per
        step; the ModelRouter multiplies these weights against the model catalog's
        capability_scores to resolve this step's dispatch model."""
        raw = self._data.get("capabilities")
        if not isinstance(raw, dict):
            return {}
        weights: dict[str, float] = {}
        for tag, weight in raw.items():
            try:
                weights[str(tag)] = float(weight)
            except (TypeError, ValueError):
                continue
        return weights

    @property
    def artifacts(self) -> list[str]:
        """The artifact(s) this step produces or updates, each identified by a schema slug (the
        artifact kind) or a URI (a specific artifact instance). Most steps declare exactly one."""
        raw = self._data.get("artifacts")
        return [str(a) for a in raw] if isinstance(raw, list) else []

    @property
    def conditions(self) -> list[Condition]:
        raw = self._data.get("conditions")
        return [Condition(cond) for cond in raw if isinstance(cond, dict)] if isinstance(raw, list) else []

    @property
    def instructions(self) -> list[str]:
        """Step-level guidance injected at session-open. Normalizes string-or-array to a list of refs.
        Each ref is a contract/repo-relative path to a `.instructions.md` file."""
        raw = self._data.get("instructions")
        if isinstance(raw, str) and raw:
            return [raw]
        if isinstance(raw, list):
            return [str(r) for r in raw if r]
        return []

    @property
    def prompts(self) -> list[str]:
        """Step-level prompt guidance injected at session-open. Normalizes string-or-array to a list of refs.
        Each ref is a contract/repo-relative path to a `.prompt.md` file."""
        raw = self._data.get("prompts")
        if isinstance(raw, str) and raw:
            return [raw]
        if isinstance(raw, list):
            return [str(r) for r in raw if r]
        return []

    @property
    def after_ids(self) -> list[str]:
        """Predecessor step ids — the `step_id` of every `type: after` condition (order-preserving,
        de-duplicated)."""
        seen: dict[str, None] = {}
        for cond in self.conditions:
            if cond.type == "after" and cond.step_id:
                seen.setdefault(cond.step_id, None)
        return list(seen.keys())

    @property
    def raw(self) -> dict[str, Any]:
        return self._data
