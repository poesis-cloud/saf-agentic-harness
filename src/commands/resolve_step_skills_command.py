"""Function 7's command: `resolve-step-skills`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.command import Command
from commands.resolve_step_skills_inquiry import ResolveStepSkillsInquiry
from services.context_resolution.step_skill_resolver import StepSkillResolver
from services.context_resolution.step_skills_report import StepSkillsReport
from utils.schema_validator import SchemaValidator


class ResolveStepSkillsCommand(Command):
    """Resolve the skills the step's session loads.

    Spec (Boundary Normalization): context injection is per-boundary, and rendering is
    the embedding mechanism's duty.
    """

    FUNCTION = "resolve-step-skills"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/resolve-step-skills.input/v1"

    def __init__(
        self, resolver: StepSkillResolver, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the step skill resolver."""
        super().__init__(schema_validator)
        self._resolver = resolver

    def _build_inquiry(self, data: Mapping[str, Any]) -> ResolveStepSkillsInquiry:
        """Build function 7's inquiry from its validated `in` object."""
        return ResolveStepSkillsInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(self, inquiry: ResolveStepSkillsInquiry) -> StepSkillsReport:
        """Unpack the inquiry into the skill resolution."""
        return self._resolver.resolve_step_skills(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
        )


__all__ = ["ResolveStepSkillsCommand"]
