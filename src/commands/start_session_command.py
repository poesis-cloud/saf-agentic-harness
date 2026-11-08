"""Function 0's command: `start-session`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.command import Command
from commands.start_session_inquiry import StartSessionInquiry
from services.session_lifecycle.session_lifecycle import SessionLifecycle
from services.session_lifecycle.session_start_report import SessionStartReport
from utils.schema_validator import SchemaValidator


class StartSessionCommand(Command):
    """Register the framework-agent session that just opened.

    Spec (function 0): session-scoped, triggered at every session start — strictly
    before any other function of that session.
    """

    FUNCTION = "start-session"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/start-session.input/v1"

    def __init__(
        self, session_lifecycle: SessionLifecycle, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the session lifecycle it registers through."""
        super().__init__(schema_validator)
        self._lifecycle = session_lifecycle

    def _build_inquiry(self, data: Mapping[str, Any]) -> StartSessionInquiry:
        """Build function 0's inquiry from its validated `in` object."""
        return StartSessionInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
            agent=data["agent"],
        )

    def execute_function(self, inquiry: StartSessionInquiry) -> SessionStartReport:
        """Unpack the inquiry into the registration call."""
        return self._lifecycle.start_session(
            agent=inquiry.agent,
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
        )


__all__ = ["StartSessionCommand"]
