"""Load `.env` files into immutable key-value mappings."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Mapping


class EnvLoader:
    """Parse simple KEY=VALUE environment files."""

    def load_environment(self, path: str | Path) -> Mapping[str, str]:
        """Load KEY=VALUE pairs while ignoring comments and blank lines."""
        values: dict[str, str] = {}
        for line_number, raw_line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            key, separator, value = line.partition("=")
            if separator == "" or key.strip() == "":
                raise ValueError(f"Malformed .env line {line_number}: expected KEY=VALUE.")

            values[key.strip()] = value.strip()

        return MappingProxyType(values)


__all__ = ["EnvLoader"]
