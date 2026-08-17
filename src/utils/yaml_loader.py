"""Load YAML files using PyYAML safe loading."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

YamlData = Mapping[str, Any] | tuple[Any, ...] | str | int | float | bool | None


def _freeze_data(value: Any) -> YamlData:
    """Convert mutable YAML containers into immutable boundary data."""
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_data(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_data(item) for item in value)
    return value


class YamlLoader:
    """Load YAML documents from disk."""

    def load_yaml(self, path: str | Path) -> YamlData:
        """Load a YAML file with `yaml.safe_load`."""
        with Path(path).open("r", encoding="utf-8") as file:
            return _freeze_data(yaml.safe_load(file))


__all__ = ["YamlLoader"]
