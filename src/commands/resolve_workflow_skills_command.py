"""Function 2's command: `resolve-workflow-skills`."""

from __future__ import annotations

from typing import Any, Mapping

from commands.command import Command
from commands.resolve_workflow_skills_inquiry import ResolveWorkflowSkillsInquiry
from services.context_resolution.workflow_skill_resolver import WorkflowSkillResolver
from services.context_resolution.workflow_skills_report import WorkflowSkillsReport
from utils.schema_validator import SchemaValidator


class ResolveWorkflowSkillsCommand(Command):
    """Resolve the skills the orchestrator's session loads.

    Spec (Boundary Normalization): skill ids are emitted as load directives — the
    rendering is the embedding mechanism's duty, never the command's.
    """

    FUNCTION = "resolve-workflow-skills"
    INPUT_CONTRACT_ID = "gsmarc://saf/contracts/api/resolve-workflow-skills.input/v1"

    def __init__(
        self, resolver: WorkflowSkillResolver, schema_validator: SchemaValidator
    ) -> None:
        """Create the command over the workflow skill resolver."""
        super().__init__(schema_validator)
        self._resolver = resolver

    def _build_inquiry(self, data: Mapping[str, Any]) -> ResolveWorkflowSkillsInquiry:
        """Build function 2's inquiry from its validated `in` object."""
        return ResolveWorkflowSkillsInquiry(
            session_id=data["sessionId"],
            parent_session_id=data.get("parentSessionId"),
        )

    def execute_function(
        self, inquiry: ResolveWorkflowSkillsInquiry
    ) -> WorkflowSkillsReport:
        """Unpack the inquiry into the skill resolution."""
        return self._resolver.resolve_workflow_skills(
            session_id=inquiry.session_id,
            parent_session_id=inquiry.parent_session_id,
        )


__all__ = ["ResolveWorkflowSkillsCommand"]
