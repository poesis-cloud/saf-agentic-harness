"""The single command interface: the harness's one JSON contract boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Mapping

from commands.inquiry import Inquiry
from errors import InquiryError
from stores.session_log_store.report import Report
from utils.schema_validator import SchemaValidator, ValidationErrorRecord


def _render_findings(function: str, findings: tuple[ValidationErrorRecord, ...]) -> str:
    """Render contract findings as the advisory prose of an `invalid-inquiry`."""
    detail = "; ".join(
        f"{finding.path or '/'}: {finding.message}" for finding in findings
    )
    return f"The {function} inquiry fails its input contract: {detail}"


class Command(ABC):
    """Bind one harness function to its two API contracts.

    Spec (Classes, `commands`): a command is the ONLY class bound to the API
    contracts — it parses and contract-validates the function's `in` object into its
    own `Inquiry` subtype, unpacks that into the service call, and returns the
    concrete `Report` subtype bound to its own output contract. It holds no domain
    logic and is hook- and host-blind.
    """

    FUNCTION: ClassVar[str]
    INPUT_CONTRACT_ID: ClassVar[str]

    def __init__(self, schema_validator: SchemaValidator) -> None:
        """Create the command over the compiled contract registry it validates with."""
        self._schema_validator = schema_validator

    def parse_inquiry(self, data: Mapping[str, Any]) -> Inquiry:
        """Contract-validate the function's `in` object into its own inquiry type.

        Spec (Outcomes rule 4): a contract-validation failure produces no report at
        all — no contract-valid report can be built when the inquiry's own session
        attribution is missing or malformed — so it is raised, to surface at the
        command exit plane exactly like a crashed invocation.
        """
        findings = self._schema_validator.validate_instance(self.INPUT_CONTRACT_ID, data)
        if findings:
            raise InquiryError(
                "invalid-inquiry", _render_findings(self.FUNCTION, findings), False
            )
        return self._build_inquiry(data)

    @abstractmethod
    def _build_inquiry(self, data: Mapping[str, Any]) -> Inquiry:
        """Build this function's own inquiry type from its validated `in` object."""

    @abstractmethod
    def execute_function(self, inquiry: Inquiry) -> Report:
        """Unpack the inquiry into the service call and answer the function's report.

        Every realization narrows the parameter to its own `Inquiry` subtype: one
        function = one input type = one output type = two contracts.
        """


__all__ = ["Command"]
