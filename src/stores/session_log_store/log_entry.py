"""One append-only log entry: a persisted report plus its write time."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from stores.session_log_store.report import Report


@dataclass(frozen=True)
class LogEntry:
    """Record exactly one completed function invocation."""

    timestamp: str
    report: Report

    def to_dict(self) -> dict[str, Any]:
        """Render the contract log-entry object."""
        return {"timestamp": self.timestamp, "report": self.report.to_dict()}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogEntry":
        """Build a log entry from a contract log-entry object."""
        return cls(timestamp=data["timestamp"], report=Report.from_dict(data["report"]))


__all__ = ["LogEntry"]
