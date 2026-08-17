"""The typed view over `conf/workflows/*.workflow.conf.yaml`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from config.workflow import Workflow
from errors import ConfigurationError


@dataclass(frozen=True)
class WorkflowCatalog:
    """Answer which workflow a slug names and which workflows an agent facilitates.

    Spec (Configuration plane): the catalog is built once at `Application` initialization,
    its advisory workflow graph already validated (references resolve, acyclic).
    """

    workflows: Mapping[str, Workflow]

    def find_workflow(self, workflow_slug: str) -> Workflow:
        """Find one workflow by slug."""
        workflow = self.workflows.get(workflow_slug)
        if workflow is None:
            raise ConfigurationError(
                "unknown-workflow",
                f"Workflow catalog was asked for the unknown workflow '{workflow_slug}'.",
                False,
            )
        return workflow

    def list_facilitated_workflows(self, actor_slug: str) -> tuple[Workflow, ...]:
        """List the workflows this agent facilitates, in catalog order."""
        return tuple(
            workflow
            for workflow in self.workflows.values()
            if workflow.facilitator == actor_slug
        )


__all__ = ["WorkflowCatalog"]
