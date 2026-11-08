"""Read the wall-clock write time of a log entry, in the journal's one rendering."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import ClassVar


class Clock:
    """Stamp the journal's ordering key as a fixed-width UTC instant.

    Spec (Logging, "Ordering — `timestamp` plus single-driver invariant"): the
    `timestamp` is the cross-log total ordering key and the store sorts the
    rendered strings, so the rendering is fixed width, UTC, and always
    `Z`-terminated — that is what makes a string sort a valid instant sort.
    """

    TIMESTAMP_FORMAT: ClassVar[str] = "%Y-%m-%dT%H:%M:%S.%fZ"

    def read_timestamp(self) -> str:
        """Read the current instant as the journal's ordering key."""
        return datetime.now(timezone.utc).strftime(self.TIMESTAMP_FORMAT)


__all__ = ["Clock"]
