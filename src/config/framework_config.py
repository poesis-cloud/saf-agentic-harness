"""FrameworkConfig — the aggregate configuration plane, built once at Application init.

Loads and contract-validates every framework configuration in one pass:

* ``conf/access-control-list.conf.yaml``  → :class:`AccessControlList`
* ``conf/model-profiles.conf.yaml``       → :class:`ModelProfiles`
* ``conf/workspace.conf.yaml``            → :class:`WorkspaceLayout`
* ``conf/workflows/*.workflow.conf.yaml`` → :class:`WorkflowCatalog` (validated lazily per file,
  eagerly at :meth:`validate_all`)

Any parse or contract violation raises :class:`ConfigError` carrying the full findings Report —
the CLI fails fast before any command logic runs, so every interaction with the harness operates
on validated configuration only.
"""

from __future__ import annotations

from pathlib import Path

from models import Report

from .access_control_list import AccessControlList
from .errors import ConfigError
from .loader import ConfigLoader
from .model_profiles import ModelProfiles
from .schema_catalog import SchemaCatalog
from .workflow_catalog import WorkflowCatalog
from .workspace_layout import WorkspaceLayout


class FrameworkConfig:
    """The validated framework configuration, aggregated."""

    def __init__(self, framework_root: Path, contracts_dir: Path, schemas: SchemaCatalog) -> None:
        loader = ConfigLoader(framework_root, contracts_dir)
        self.loader = loader
        self.access_control_list = AccessControlList(loader.load("access-control-list"))
        self.model_profiles = ModelProfiles(loader.load("model-profiles"))
        self.workspace_layout = WorkspaceLayout(loader.load("workspace"))
        self.workflows = WorkflowCatalog(framework_root, loader)
        self.schemas = schemas

    @classmethod
    def detect(cls, workspace) -> "FrameworkConfig":
        """Build the aggregate from a Workspace (framework root + harness contracts dir)."""
        return cls(workspace.framework_root, workspace.schemas_dir, SchemaCatalog(workspace))

    def validate_all(self) -> Report:
        """Eagerly validate the per-file config families too (workflows), returning findings.
        The four single-file configs already validated in __init__ (they raise on construction)."""
        report = Report()
        try:
            self.workflows.all()
        except ConfigError as exc:
            report.extend(exc.report)
        return report


__all__ = ["FrameworkConfig"]
