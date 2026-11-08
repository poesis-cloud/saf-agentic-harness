"""Load JSON files into immutable plain data."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

JsonData = Mapping[str, Any] | tuple[Any, ...] | str | int | float | bool | None


def _freeze_data(value: Any) -> JsonData:
    """Convert mutable JSON containers into immutable boundary data."""
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_data(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_data(item) for item in value)
    return value


class JsonLoader:
    """Load JSON documents from disk."""

    def load_json(self, path: str | Path) -> JsonData:
        """Load a JSON file into immutable plain data."""
        with Path(path).open("r", encoding="utf-8") as file:
            return _freeze_data(json.load(file))


__all__ = ["JsonLoader"]
