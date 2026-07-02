"""Domain models — the framework's entities, one class per file.

Each model abstracts a file (or a fragment of one): `Workflow` ⇄ workflow.yaml, `Artifact`
⇄ a portfolio unit, `Log` ⇄ a run log, `ArtifactSchema` ⇄ an artifact schema. `Finding` /
`Report` are the result entities. Models depend only on the `text` kernel — never on
mappers, services, or the CLI.
"""

from __future__ import annotations

from .artifact import Artifact
from .artifact_schema import ArtifactSchema
from .condition import Condition
from .finding import Finding
from .log import Log, LogEntry
from .report import Report
from .section_spec import SectionSpec
from .step import Step
from .workflow import Workflow

__all__ = [
    "Artifact",
    "ArtifactSchema",
    "Condition",
    "Finding",
    "Log",
    "LogEntry",
    "Report",
    "SectionSpec",
    "Step",
    "Workflow",
]
