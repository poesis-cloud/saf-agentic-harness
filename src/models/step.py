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
    def role(self) -> str:
        """The bench role for model routing (role-default tier floor). Defaults to the actor with
        any leading ``@`` stripped, so a step that does not declare ``role`` still routes."""
        raw = self._data.get("role")
        if isinstance(raw, str) and raw:
            return raw
        actor = self._data.get("actor")
        return str(actor).lstrip("@") if isinstance(actor, str) else ""

    @property
    def risk(self) -> str:
        """Routing risk class — low | medium | critical (raises the tier floor)."""
        return str(self._data.get("risk") or "")

    @property
    def complexity(self) -> str:
        """Routing complexity class — simple | involved | complex (raises the tier floor)."""
        return str(self._data.get("complexity") or "")

    @property
    def tags(self) -> list[str]:
        """Capability tags scored by ``ModelRouter`` to resolve the concrete model."""
        raw = self._data.get("tags")
        return [str(t) for t in raw] if isinstance(raw, list) else []

    @property
    def config(self) -> str:
        """Config profile — deterministic | audit | exploratory | creative."""
        return str(self._data.get("config") or "")

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
        """Predecessor step ids — the values of the step's `after` conditions."""
        return self.ref_values("after")

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
