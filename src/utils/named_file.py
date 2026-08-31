"""Resolve a slug to exactly one file under a canonical framework directory."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from errors import ConfigurationError


def index_unique_stems(directory: Path, suffix: str) -> Mapping[str, Path]:
    """Index ``*{suffix}`` files by filename stem; refuse a stem that appears twice.

    Spec (slug convention): the slug is the filename stem. Nested folders under the
    canonical directory are allowed; the stem must still be unique so a slug never
    names two files.
    """
    index: dict[str, Path] = {}
    for path in sorted(path for path in directory.rglob(f"*{suffix}") if path.is_file()):
        slug = path.name[: -len(suffix)]
        existing = index.get(slug)
        if existing is not None:
            raise ConfigurationError(
                "duplicate-filename-stem",
                f"Filename stem '{slug}{suffix}' is declared twice: '{existing}' and "
                f"'{path}'.",
                False,
            )
        index[slug] = path
    return index


__all__ = ["index_unique_stems"]
