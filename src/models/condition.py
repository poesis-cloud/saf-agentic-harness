"""One entry of a step's flat `conditions` list (kind/type/expression/value/id)."""

from __future__ import annotations

from typing import Any


class Condition:
    """A single precondition / postcondition / invariant on a step.

    Wraps the raw condition mapping and exposes its four orthogonal attributes plus
    expression-classification helpers. Structural refs (after/input/output) carry
    `expression: ref`; judgments carry `cel` (pre/post) or `instruction` (invariant).
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
    def expression(self) -> str:
        return str(self._data.get("expression") or "")

    @property
    def value(self) -> str:
        return str(self._data.get("value") or "")

    @property
    def id(self) -> str | None:
        raw = self._data.get("id")
        return str(raw) if raw not in (None, "") else None

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
