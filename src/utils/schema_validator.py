"""Compile JSON Schema 2020-12 contracts and validate instances."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from utils.json_loader import JsonLoader


@dataclass(frozen=True)
class ValidationErrorRecord:
    """Plain validation error record with a JSON Pointer path and message."""

    path: str
    message: str


def _thaw_data(value: Any) -> Any:
    """Convert immutable loader data into jsonschema-compatible containers."""
    if isinstance(value, Mapping):
        return {key: _thaw_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_data(item) for item in value]
    return value


def _escape_pointer_segment(segment: object) -> str:
    """Escape one JSON Pointer path segment."""
    return str(segment).replace("~", "~0").replace("/", "~1")


def _build_json_pointer(path: Iterable[object]) -> str:
    """Build a JSON Pointer from a jsonschema error path."""
    segments = tuple(path)
    if not segments:
        return ""
    return "/" + "/".join(_escape_pointer_segment(segment) for segment in segments)


@dataclass(frozen=True)
class SchemaValidator:
    """Validate plain data against compiled contracts addressed by `$id`."""

    _schemas: Mapping[str, Mapping[str, Any]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    _registry: Registry = field(default_factory=Registry)
    _json_loader: JsonLoader = field(default_factory=JsonLoader)

    @classmethod
    def compile_contracts(
        cls,
        schema_paths: Iterable[str | Path],
        json_loader: JsonLoader | None = None,
    ) -> "SchemaValidator":
        """Compile contract files into a validator registry keyed by canonical `$id`."""
        loader = json_loader or JsonLoader()
        schemas: dict[str, Mapping[str, Any]] = {}
        resources: list[tuple[str, Resource[Any]]] = []

        for schema_path in sorted(Path(path) for path in schema_paths):
            schema = _thaw_data(loader.load_json(schema_path))
            if not isinstance(schema, dict) or not isinstance(schema.get("$id"), str):
                raise ValueError(f"Schema {schema_path} must declare a string $id.")

            schema_id = schema["$id"]
            if schema_id in schemas:
                raise ValueError(f"Duplicate schema $id: {schema_id}.")

            schemas[schema_id] = schema
            resources.append((schema_id, Resource.from_contents(schema)))

        registry = Registry().with_resources(resources)
        return cls(
            _schemas=MappingProxyType(schemas),
            _registry=registry,
            _json_loader=loader,
        )

    def validate_instance(
        self,
        schema_id: str,
        instance: Any,
    ) -> tuple[ValidationErrorRecord, ...]:
        """Return raw validation reports for an instance; never raise for invalid data."""
        if schema_id not in self._schemas:
            raise KeyError(schema_id)

        validator = Draft202012Validator(self._schemas[schema_id], registry=self._registry)
        errors = sorted(
            validator.iter_errors(_thaw_data(instance)),
            key=lambda error: tuple(str(part) for part in error.path),
        )
        return tuple(
            ValidationErrorRecord(path=_build_json_pointer(error.path), message=error.message)
            for error in errors
        )


__all__ = ["SchemaValidator", "ValidationErrorRecord"]
