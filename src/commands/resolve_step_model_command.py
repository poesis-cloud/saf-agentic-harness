"""Function 4's command: `resolve-step-model`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.command import Command
from commands.resolve_step_model_inquiry import ResolveStepModelInquiry
from services.model_resolution.model_profile_report import ModelProfileReport
from services.model_resolution.step_model_resolver import StepModelResolver
from utils.schema_validator import SchemaValidator


class ResolveStepModelCommand(Command):
    """Bind the in-flight step to the model profile serving its dispatch.

    Spec (function 4): independent of function 3 and of the acting agent.
    """

    FUNCTION = "resolve-step-model"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/resolve-step-model.input/v1"

    def __init__(
        self, model_resolver: StepModelResolver, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the model resolver it binds through."""
        super().__init__(schema_validator)
        self._model_resolver = model_resolver

    def _build_inquiry(self, data: Mapping[str, Any]) -> ResolveStepModelInquiry:
        """Build function 4's inquiry from its validated `in` object."""
        return ResolveStepModelInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(self, inquiry: ResolveStepModelInquiry) -> ModelProfileReport:
        """Unpack the inquiry into the model-binding call."""
        return self._model_resolver.resolve_step_model(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
        )


__all__ = ["ResolveStepModelCommand"]
