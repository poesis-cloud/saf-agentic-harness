"""Function 0's result: the registration report of one opened session."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.session_lifecycle.session_ref import SessionRef
from stores.session_log_store.report import Report


@dataclass(frozen=True)
class SessionStartReport(Report):
    """Report the opened session, absent on the `not-applicable` and error outcomes."""

    session: SessionRef | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract report, adding `session` only where the outcome carries it."""
        rendered = super().to_dict()
        if self.session is not None:
            rendered["session"] = self.session.to_dict()
        return rendered


__all__ = ["SessionStartReport"]
