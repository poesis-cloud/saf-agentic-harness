"""Function 11's result: the closing report of one ended session."""

from __future__ import annotations

from dataclasses import dataclass

from stores.session_log_store.report import Report


@dataclass(frozen=True)
class SessionEndReport(Report):
    """Report a session's ending — the bare envelope: its identity is its contract."""


__all__ = ["SessionEndReport"]
