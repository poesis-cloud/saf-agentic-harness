"""Configuration plane — the framework's conf/*.conf.yaml files, parsed AND validated as one act.

The workspace content is the database the harness CRUDs domain entities from (models/ via
mappers/); the conf/ directory is the framework's static configuration. This package owns that
second plane end to end: each conf file has a contract schema in harness/contracts/
(<name>.conf.schema.json), a typed view class here, and one shared loader that parses the YAML and
validates it against the contract in the same step (parsing and validation are the same act — an
unvalidated parse never escapes this package). `FrameworkConfig` aggregates the four views and is
built once at Application initialization: every CLI interaction (check/hook/orchestrate) fails fast
on an invalid configuration before any command logic runs.
"""

from .errors import ConfigError
from .loader import ConfigLoader, HARNESS_CONTRACTS_DIR
from .access_control_list import AccessControlList
from .framework_layout import FrameworkLayout
from .model_profiles import ModelProfiles
from .workspace_layout import WorkspaceLayout
from .condition import Condition
from .step import Step
from .workflow import Workflow
from .workflow_catalog import WorkflowCatalog
from .schema_catalog import SchemaCatalog
from .framework_config import FrameworkConfig

__all__ = [
    "AccessControlList",
    "Condition",
    "ConfigError",
    "ConfigLoader",
    "FrameworkConfig",
    "FrameworkLayout",
    "HARNESS_CONTRACTS_DIR",
    "ModelProfiles",
    "SchemaCatalog",
    "Step",
    "Workflow",
    "WorkflowCatalog",
    "WorkspaceLayout",
]
