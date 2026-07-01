"""WorkflowRepository — maps workflow.yaml files to `Workflow` entities."""

from __future__ import annotations

from pathlib import Path

from models import Workflow
from text import parse_contract
from .workspace import Workspace


class WorkflowRepository:
    """The data-mapper for the workflow constitution.

    `load`/`all`/`find` deserialize workflow.yaml into `Workflow` entities (lenient
    `parse_contract`, matching the runtime readers). Strict schema validation is the
    `WorkflowChecker`'s job, not the mapper's.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def paths(self) -> list[Path]:
        return sorted(self.workspace.skills_root.glob("**/workflow.yaml"))

    def load(self, path: Path) -> Workflow:
        return Workflow(parse_contract(self.workspace.read_text(path)), path)

    def all(self) -> list[Workflow]:
        return [self.load(path) for path in self.paths()]

    def find(self, orchestration_id: str) -> Workflow | None:
        for path in self.paths():
            workflow = self.load(path)
            if str(workflow.id) == orchestration_id:
                return workflow
        return None
