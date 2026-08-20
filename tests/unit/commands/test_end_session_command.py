"""Tests for `EndSessionCommand` — function 11, `end-session`."""

from __future__ import annotations

import pytest

from command_fixtures import RecordingService
from commands.end_session_command import EndSessionCommand
from commands.end_session_inquiry import EndSessionInquiry
from errors import InquiryError
from utils import SchemaValidator


class TestEndSessionCommand:
    """Function 11's command: the closing boundary's contract binding."""

    def test_parses_its_in_object_against_its_own_input_contract(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (contracts/api/end-session.input): the shared input envelope,
        unextended — `parentSessionId` is accepted but unused.
        """
        command = EndSessionCommand(RecordingService("end-session"), schema_validator)

        inquiry = command.parse_inquiry({"sessionId": "s1", "parentSessionId": "p1"})

        assert inquiry == EndSessionInquiry(session_id="s1", parent_session_id="p1")

    def test_refuses_an_inquiry_whose_session_id_is_no_safe_slug(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Logging, sanitization): `sessionId` becomes a log filename, so a raw id
        is a path-traversal vector the contract rejects.
        """
        command = EndSessionCommand(RecordingService("end-session"), schema_validator)

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "../escape"})

        assert failure.value.code == "invalid-inquiry"

    def test_unpacks_only_the_session_the_ending_entry_closes(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (function 11, invariant 4 / contracts/api/end-session.input): the parent
        chain was recorded by this session's own start entry, so ending takes the
        session id alone.
        """
        lifecycle = RecordingService("end-session", "ended")
        command = EndSessionCommand(lifecycle, schema_validator)

        report = command.execute_function(
            EndSessionInquiry(session_id="s1", parent_session_id="p1")
        )

        assert lifecycle.calls == [{"session_id": "s1"}]
        assert report is lifecycle.report
