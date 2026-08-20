"""Function 1's command: `resolve-workflow-instructions`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.command import Command
from commands.resolve_workflow_instructions_inquiry import (
    ResolveWorkflowInstructionsInquiry,
)
from services.context_resolution.workflow_instruction_resolver import (
    WorkflowInstructionResolver,
)
from services.context_resolution.workflow_instructions_report import (
    WorkflowInstructionsReport,
)
from utils.schema_validator import SchemaValidator


class ResolveWorkflowInstructionsCommand(Command):
    """Resolve the workflow-context guidance the orchestrator's session loads.

    Spec (Boundary Normalization): re-resolution at every session-started boundary is
    mandatory, not an optimization.
    """

    FUNCTION = "resolve-workflow-instructions"
    INPUT_CONTRACT_ID = (
        "gsmarc://saf/contracts/api/resolve-workflow-instructions.input/v1"
    )

    def __init__(
        self, resolver: WorkflowInstructionResolver, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the workflow instruction resolver."""
        super().__init__(schema_validator)
        self._resolver = resolver

    def _build_inquiry(
        self, data: Mapping[str, Any]
    ) -> ResolveWorkflowInstructionsInquiry:
        """Build function 1's inquiry from its validated `in` object."""
        return ResolveWorkflowInstructionsInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(
        self, inquiry: ResolveWorkflowInstructionsInquiry
    ) -> WorkflowInstructionsReport:
        """Unpack the inquiry into the instruction resolution."""
        return self._resolver.resolve_workflow_instructions(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
        )


__all__ = ["ResolveWorkflowInstructionsCommand"]
