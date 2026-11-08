"""The version stamp every session-bound function checks (core, Harness compatibility)."""

from __future__ import annotations

from typing import Any, Mapping

from errors import StateError
from harness_version import HARNESS_VERSION
from stores.session_log_store import Log

HARNESS_VERSION_CHANGED = "harness-version-changed"
_START_FUNCTION = "start-session"


def find_session_harness_version(log: Log) -> str | None:
    """Answer the harness version the session's start entry records, if it records one."""
    for entry in log.entries:
        if entry.report.context.function != _START_FUNCTION:
            continue
        session: Mapping[str, Any] | None = entry.report.payload.get("session")
        if session is not None:
            version = session.get("harnessVersion")
            return version if isinstance(version, str) else None
    return None


def refuse_other_harness_version(log: Log, installed: str = HARNESS_VERSION) -> None:
    """Refuse a call against a session another harness version started.

    Spec: `state-error` / `harness-version-changed`, never journaled — one harness
    version never writes into a log another version started. Function 11 is exempt and
    never calls this.
    """
    stamped = find_session_harness_version(log)
    if stamped != installed:
        raise StateError(
            HARNESS_VERSION_CHANGED,
            f"The session was started by harness {stamped or 'of an unknown version'}; "
            f"installed {installed}: start a new session.",
            False,
        )


__all__ = [
    "HARNESS_VERSION_CHANGED",
    "find_session_harness_version",
    "refuse_other_harness_version",
]
