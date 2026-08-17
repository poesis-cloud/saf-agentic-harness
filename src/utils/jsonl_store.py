"""Persist and load append-only JSONL records."""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


def _freeze_data(value: Any) -> Any:
    """Convert mutable JSON containers into immutable boundary data."""
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_data(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_data(item) for item in value)
    return value


def _thaw_data(value: Any) -> Any:
    """Convert immutable boundary data into JSON-serializable containers."""
    if isinstance(value, Mapping):
        return {key: _thaw_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_data(item) for item in value]
    return value


class JsonlStore:
    """Store one JSON object per line in an append-only file."""

    def load_entries(self, path: str | Path) -> tuple[Mapping[str, Any], ...]:
        """Load JSONL entries as immutable mappings."""
        entries: list[Mapping[str, Any]] = []
        with Path(path).open("r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    raise ValueError("JSONL entries must be JSON objects.")
                entries.append(_freeze_data(entry))
        return tuple(entries)

    def append_entry(self, path: str | Path, entry: Mapping[str, Any]) -> None:
        """Append one entry as a single JSON line."""
        with Path(path).open("a", encoding="utf-8") as file:
            file.write(json.dumps(_thaw_data(entry), ensure_ascii=False, separators=(",", ":")))
            file.write("\n")


__all__ = ["JsonlStore"]
