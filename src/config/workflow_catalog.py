"""WorkflowCatalog — loads and contract-validates the conf/workflows/ configuration family.

Each `conf/workflows/*.workflow.conf.yaml` file is one workflow configuration; every file is
parsed AND validated against `harness/contracts/workflow.conf.schema.json` in the same act (via
`ConfigLoader.load_path`). Semantic rules JSON Schema cannot express (unique step ids, acyclic
`after` DAG, CEL compilation) remain the `WorkflowChecker`'s job at `make verify`.
"""

from __future__ import annotations

from pathlib import Path

from .loader import ConfigLoader
from .workflow import Workflow


class WorkflowCatalog:
    """The configuration catalog for workflows: paths, load, all, find."""

    def __init__(self, framework_root: Path, loader: ConfigLoader) -> None:
        self.framework_root = framework_root
        self.loader = loader
        self._all: list[Workflow] | None = None

    def paths(self) -> list[Path]:
        return sorted((self.framework_root / "conf" / "workflows").glob("*.workflow.conf.yaml"))

    def load(self, path: Path) -> Workflow:
        """Parse + contract-validate one workflow configuration file (raises ConfigError)."""
        return Workflow(self.loader.load_path(path, "workflow"), path)

    def all(self) -> list[Workflow]:
        if self._all is None:
            self._all = [self.load(path) for path in self.paths()]
        return self._all

    def find(self, orchestration_id: str) -> Workflow | None:
        for workflow in self.all():
            if str(workflow.id) == orchestration_id:
                return workflow
        return None


__all__ = ["WorkflowCatalog"]
