"""Tests for the single command interface: the harness's JSON contract boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from command_fixtures import build_report
from commands.command import Command
from commands.inquiry import Inquiry
from errors import InquiryError
from stores.session_log_store.report import Report
from utils import SchemaValidator


@dataclass(frozen=True)
class _EndSessionLikeInquiry(Inquiry):
    """The bare envelope inquiry the probe command parses into."""


class _ProbeCommand(Command):
    """A command bound to the bare-envelope end-session input contract."""

    FUNCTION = "end-session"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/end-session.input/v1"

    def _build_inquiry(self, data: Mapping[str, Any]) -> Inquiry:
        return _EndSessionLikeInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(self, inquiry: Inquiry) -> Report:
        return build_report("end-session", "ended")


class TestCommand:
    """The command boundary: contract validation, then the typed inquiry."""

    def test_parses_a_contract_valid_in_object_into_its_own_inquiry_type(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): a command parses and contract-validates the
        function's `in` object into its OWN `Inquiry` subtype — one function = one input
        type = one output type = two contracts.
        """
        command = _ProbeCommand(schema_validator)

        inquiry = command.parse_inquiry({"sessionId": "s1", "parentSessionId": "p1"})

        assert isinstance(inquiry, _EndSessionLikeInquiry)
        assert (inquiry.session_id, inquiry.parent_session_id) == ("s1", "p1")

    def test_refuses_an_in_object_its_input_contract_rejects(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Outcomes rule 1): everything wrong with the inquiry itself is an
        `inquiry-error`; a failure of the input contract carries code `invalid-inquiry`.
        """
        command = _ProbeCommand(schema_validator)

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "Not A Slug"})

        assert failure.value.code == "invalid-inquiry"
        assert failure.value.status == "inquiry-error"

    def test_refuses_an_in_object_missing_the_session_attribution(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Outcomes rule 4): a contract-valid report cannot be built when the
        inquiry's own `sessionId` is missing or malformed, so the failure surfaces at the
        command exit plane rather than as a report.
        """
        command = _ProbeCommand(schema_validator)

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({})

        assert failure.value.code == "invalid-inquiry"

    def test_refuses_an_in_object_carrying_an_undeclared_property(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Source layout, contract dialect): a rooted schema uses
        `unevaluatedProperties: false`, so a property no contract declares is rejected —
        the command is the ONLY class bound to the API contracts.
        """
        command = _ProbeCommand(schema_validator)

        with pytest.raises(InquiryError) as failure:
            command.parse_inquiry({"sessionId": "s1", "workflowSlug": "planning"})

        assert failure.value.code == "invalid-inquiry"

    def test_never_builds_an_inquiry_from_data_the_contract_rejected(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Outcomes rule 4): a contract-validation failure produces NO report at
        all — the invocation never reaches the service behind the command.
        """
        built: list[Mapping[str, Any]] = []

        class _TracingCommand(_ProbeCommand):
            def _build_inquiry(self, data: Mapping[str, Any]) -> Inquiry:
                built.append(data)
                return super()._build_inquiry(data)

        with pytest.raises(InquiryError):
            _TracingCommand(schema_validator).parse_inquiry({"sessionId": "BAD"})

        assert built == []

    def test_names_the_function_and_the_input_contract_it_is_bound_to(
        self, schema_validator: SchemaValidator
    ) -> None:
        """Spec (Classes, `commands`): a command's identity is its contract's `$id`, not
        its shape — seven of the twelve inquiries add no field of their own.
        """
        command = _ProbeCommand(schema_validator)

        assert command.FUNCTION == "end-session"
        assert command.INPUT_CONTRACT_ID == "gsmarc://saf/contracts/api/end-session.input/v1"
