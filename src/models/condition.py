"""One entry of a step's flat `conditions` list (kind/type/id/step_id/set_selector/set_predicate)."""

from __future__ import annotations

from typing import Any


class Condition:
    """A single precondition / postcondition on a step.

    Wraps the raw condition mapping and exposes its attributes: kind (precondition/postcondition),
    type (after/state), id (stable identifier), step_id (for after), set_selector (for state),
    and set_predicate (for state). Also exposes legacy expression classification for backward
    compatibility during migration from the old condition model.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    @property
    def kind(self) -> str:
        return str(self._data.get("kind") or "")

    @property
    def type(self) -> str:
        return str(self._data.get("type") or "")

    @property
    def id(self) -> str | None:
        raw = self._data.get("id")
        return str(raw) if raw not in (None, "") else None

    @property
    def step_id(self) -> str | None:
        raw = self._data.get("step_id")
        return str(raw) if raw not in (None, "") else None

    @property
    def set_selector(self) -> dict[str, Any] | None:
        """The selector for type: state conditions. Defines artifact set to evaluate."""
        raw = self._data.get("set_selector")
        return raw if isinstance(raw, dict) else None

    @property
    def set_predicate(self) -> str | None:
        """The CEL boolean assertion for type: state conditions."""
        raw = self._data.get("set_predicate")
        return str(raw) if raw not in (None, "") else None

    # Legacy properties for backward compatibility during migration
    @property
    def expression(self) -> str:
        return str(self._data.get("expression") or "")

    @property
    def value(self) -> str:
        return str(self._data.get("value") or "")

    @property
    def is_ref(self) -> bool:
        return self.expression == "ref"

    @property
    def is_cel(self) -> bool:
        return self.expression == "cel"

    @property
    def is_instruction(self) -> bool:
        return self.expression == "instruction"

    @property
    def raw(self) -> dict[str, Any]:
        return self._data
