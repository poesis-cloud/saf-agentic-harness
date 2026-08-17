"""One session log: the session id and its ordered entries."""

from __future__ import annotations

from dataclasses import dataclass

from stores.session_log_store.log_entry import LogEntry


@dataclass(frozen=True)
class Log:
    """One session's append-only journal, hydrated in file order."""

    session_id: str
    entries: tuple[LogEntry, ...]


__all__ = ["Log"]
