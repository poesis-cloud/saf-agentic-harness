"""Function 3's command: `resolve-step`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.command import Command
from commands.resolve_step_inquiry import ResolveStepInquiry
from services.step_resolution.step_resolution_report import StepResolutionReport
from services.step_resolution.step_resolver import StepResolver
from utils.schema_validator import SchemaValidator


class ResolveStepCommand(Command):
    """Resolve the workflow instance's next eligible step.

    Spec (Classes, `commands`): no command composes services — functions 3 and 4 are
    fully independent, and whoever needs both composes them outside the harness core.
    """

    FUNCTION = "resolve-step"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/resolve-step.input/v1"

    def __init__(
        self, step_resolver: StepResolver, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the step resolver it resolves through."""
        super().__init__(schema_validator)
        self._step_resolver = step_resolver

    def _build_inquiry(self, data: Mapping[str, Any]) -> ResolveStepInquiry:
        """Build function 3's inquiry from its validated `in` object."""
        return ResolveStepInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
            workflow_slug=data["workflowSlug"],
        )

    def execute_function(self, inquiry: ResolveStepInquiry) -> StepResolutionReport:
        """Unpack the inquiry into the resolution call."""
        return self._step_resolver.resolve_step(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
            workflow_slug=inquiry.workflow_slug,
        )


__all__ = ["ResolveStepCommand"]
