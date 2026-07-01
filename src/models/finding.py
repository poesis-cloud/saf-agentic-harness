"""A single validation finding (error or warning) about one artifact / condition."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Finding:
    severity: str
    path: str
    message: str
    condition_id: str | None = None
    expected: str | None = None
    actual: str | None = None
    suggestion: str | None = None
