"""FrameworkConfig — the aggregate configuration plane, built once at Application init.

Loads and contract-validates every framework configuration in one pass:

* ``conf/framework.conf.yaml``            → :class:`FrameworkLayout` (the framework's own paths)
* ``conf/access-control-list.conf.yaml``  → :class:`AccessControlList`
* ``conf/model-profiles.conf.yaml``       → :class:`ModelProfiles`
* ``conf/workspace.conf.yaml``            → :class:`WorkspaceLayout` (the workspace blueprint)
* ``conf/workflows/*.workflow.conf.yaml`` → :class:`WorkflowCatalog` (validated lazily per file,
  eagerly at :meth:`validate_all`)

Any parse or contract violation raises :class:`ConfigError` carrying the full findings Report —
the CLI fails fast before any command logic runs, so every interaction with the harness operates
on validated configuration only. Terminology: the FRAMEWORK is the application embedding the
harness; the WORKSPACE is the data plane the harness checks (this framework's portfolio) — the
two are never conflated.
"""

from __future__ import annotations

from pathlib import Path

from models import Report

from .access_control_list import AccessControlList
from .errors import ConfigError
from .framework_layout import FrameworkLayout
from .loader import ConfigLoader, HARNESS_ADAPTERS_DIR
from .model_profiles import ModelProfiles
from .schema_catalog import SchemaCatalog
from .workflow_catalog import WorkflowCatalog
from .workspace_layout import WorkspaceLayout


class FrameworkConfig:
    """The validated framework configuration, aggregated."""

    def __init__(self, workspace, contracts_dir: Path | None = None) -> None:
        framework_root = workspace.framework_root
        loader = ConfigLoader(framework_root, contracts_dir)
        self.loader = loader
        self.framework_layout = FrameworkLayout(loader.load("framework"))
        self.access_control_list = AccessControlList(loader.load("access-control-list"))
        self.model_profiles = ModelProfiles(loader.load("model-profiles"))
        self.workspace_layout = WorkspaceLayout(loader.load("workspace"))
        self.workflows = WorkflowCatalog(framework_root, loader)
        self.schemas = SchemaCatalog(workspace, self.framework_layout)

    @classmethod
    def detect(cls, workspace) -> "FrameworkConfig":
        """Build the aggregate from a Workspace (the shared filesystem context)."""
        return cls(workspace)

    def adapter_binding(self, env: str) -> dict:
        """The host adapter binding (harness/adapters/<env>/tools.yaml), parsed AND validated
        against the adapter contract in one act. INTERNAL configuration: harness-owned and
        resolved structurally, but loaded through the same discipline as the framework's
        external conf/*.conf.yaml files. An unknown env or invalid binding raises ConfigError."""
        return self.loader.load_path(HARNESS_ADAPTERS_DIR / env / "tools.yaml", "adapter")

    def validate_all(self) -> Report:
        """Eagerly validate the per-file config families too (workflows), returning findings.
        The single-file configs already validated in __init__ (they raise on construction)."""
        report = Report()
        try:
            self.workflows.all()
            report.extend(self.workflows.catalog_report())
        except ConfigError as exc:
            report.extend(exc.report)
        return report


__all__ = ["FrameworkConfig"]
