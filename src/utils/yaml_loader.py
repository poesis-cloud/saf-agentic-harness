"""Load YAML files using PyYAML safe loading."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

YamlData = Mapping[str, Any] | tuple[Any, ...] | str | int | float | bool | None

_TIMESTAMP_TAG = "tag:yaml.org,2002:timestamp"


class JsonDataSafeLoader(yaml.SafeLoader):
    """A `SafeLoader` whose scalars stay inside the JSON data model.

    YAML 1.1 resolves an unquoted `2026-11-04` to `datetime.date`, a value no JSON Schema
    type admits. Data validated against JSON Schema (artifacts) keeps it as the string it
    reads as, whether the timestamp is implicit or tagged `!!timestamp`.
    """


JsonDataSafeLoader.yaml_implicit_resolvers = {
    first: [(tag, pattern) for tag, pattern in resolvers if tag != _TIMESTAMP_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
JsonDataSafeLoader.add_constructor(_TIMESTAMP_TAG, JsonDataSafeLoader.construct_yaml_str)


def load_json_data(content: str) -> Any:
    """Parse YAML text into JSON-data-model values (see `JsonDataSafeLoader`)."""
    return yaml.load(content, Loader=JsonDataSafeLoader)  # noqa: S506 - a SafeLoader


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

    def parse_json_data(self, content: str) -> YamlData:
        """Parse YAML text as JSON data: timestamps stay strings (`JsonDataSafeLoader`)."""
        return _freeze_data(load_json_data(content))


__all__ = ["JsonDataSafeLoader", "YamlLoader", "load_json_data"]
