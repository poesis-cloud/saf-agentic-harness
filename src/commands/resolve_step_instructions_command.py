"""Function 6's command: `resolve-step-instructions`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.command import Command
from commands.resolve_step_instructions_inquiry import ResolveStepInstructionsInquiry
from services.context_resolution.step_instruction_resolver import (
    StepInstructionResolver,
)
from services.context_resolution.step_instructions_report import StepInstructionsReport
from utils.schema_validator import SchemaValidator


class ResolveStepInstructionsCommand(Command):
    """Resolve the behavioral guidance the step's session loads.

    Spec (Boundary Normalization): the step-started boundary can only inject — the
    veto lives at step-starting (function 5).
    """

    FUNCTION = "resolve-step-instructions"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/resolve-step-instructions.input/v1"

    def __init__(
        self, resolver: StepInstructionResolver, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the step instruction resolver."""
        super().__init__(schema_validator)
        self._resolver = resolver

    def _build_inquiry(self, data: Mapping[str, Any]) -> ResolveStepInstructionsInquiry:
        """Build function 6's inquiry from its validated `in` object."""
        return ResolveStepInstructionsInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(
        self, inquiry: ResolveStepInstructionsInquiry
    ) -> StepInstructionsReport:
        """Unpack the inquiry into the instruction resolution."""
        return self._resolver.resolve_step_instructions(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
        )


__all__ = ["ResolveStepInstructionsCommand"]
