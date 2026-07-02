"""One structurant step — a single actor's turn, with its flat conditions list."""

from __future__ import annotations

from typing import Any

from .condition import Condition


class Step:
    """A workflow step: one `actor`, one `kind`, and a flat `conditions` list.

    Exposes the structural wiring (`after_ids`, `ref_values`) and the CEL expressions
    (`cel_exprs`) the checkers read, without leaking the raw mapping shape.
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
    def kind(self) -> str:
        return str(self._data.get("kind") or "")

    @property
    def delegates_to(self) -> Any:
        return self._data.get("delegates_to")

    @property
    def skills(self) -> list[str]:
        """The skill ids the dispatched agent loads for this step (per-step, not per-workflow)."""
        raw = self._data.get("skills")
        return [str(s) for s in raw] if isinstance(raw, list) else []

    @property
    def output(self) -> str:
        """The single artifact KIND this step produces or updates (effect-determinism)."""
        return str(self._data.get("output") or "")

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

    def ref_values(self, condition_type: str) -> list[str]:
        """The `value` of every structural condition of the given type (after/input/output)."""
        return [cond.value for cond in self.conditions if cond.type == condition_type and cond.is_ref and cond.value]

    @property
    def after_ids(self) -> list[str]:
        """Predecessor step ids. Reads the new model (`type: after` → `step_id`) and, for
        backward compatibility during migration, the legacy `after`/`ref` `value`."""
        ids = [cond.step_id for cond in self.conditions if cond.type == "after" and cond.step_id]
        ids.extend(self.ref_values("after"))  # legacy expression: ref, value: <step_id>
        # preserve order, drop duplicates
        seen: dict[str, None] = {}
        for step_id in ids:
            seen.setdefault(step_id, None)
        return list(seen.keys())

    @property
    def cel_exprs(self) -> list[tuple[str, str]]:
        """(condition_id, cel_value) for every condition whose `expression` is `cel`."""
        result: list[tuple[str, str]] = []
        for cond in self.conditions:
            if not cond.is_cel:
                continue
            raw = cond.raw.get("value")
            if isinstance(raw, str) and raw:
                result.append((cond.id or "", raw))
        return result

    @property
    def raw(self) -> dict[str, Any]:
        return self._data
