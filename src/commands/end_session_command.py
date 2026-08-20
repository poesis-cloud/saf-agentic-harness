"""Function 11's command: `end-session`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.command import Command
from commands.end_session_inquiry import EndSessionInquiry
from services.session_lifecycle.session_end_report import SessionEndReport
from services.session_lifecycle.session_lifecycle import SessionLifecycle
from utils.schema_validator import SchemaValidator


class EndSessionCommand(Command):
    """Close the session's log with its final entry.

    Spec (function 11): best-effort and idempotent — the one function exempt from the
    C8 refusal.
    """

    FUNCTION = "end-session"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/end-session.input/v1"

    def __init__(
        self, session_lifecycle: SessionLifecycle, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the session lifecycle it closes through."""
        super().__init__(schema_validator)
        self._lifecycle = session_lifecycle

    def _build_inquiry(self, data: Mapping[str, Any]) -> EndSessionInquiry:
        """Build function 11's inquiry from its validated `in` object."""
        return EndSessionInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(self, inquiry: EndSessionInquiry) -> SessionEndReport:
        """Unpack the inquiry into the closing call: the session id alone."""
        return self._lifecycle.end_session(session_id=inquiry.session_id)


__all__ = ["EndSessionCommand"]
