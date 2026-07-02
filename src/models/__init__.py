"""Domain models — the framework's entities, one class per file.

Each model abstracts a file (or a fragment of one): `Workflow` ⇄ workflow.yaml, `Artifact`
⇄ a portfolio unit, `Log` ⇄ a run log, `Section` ⇄ a markdown section. `Finding` /
`Report` are the result entities. Models depend only on the `text` kernel — never on
mappers, services, or the CLI.

Artifact schemas are now pure data (raw dicts), not reified classes (Alternative 1).
"""

from __future__ import annotations

from .artifact import Artifact
from .condition import Condition
from .finding import Finding
from .log import Log, LogEntry
from .report import Report
from .section import Section
from .step import Step
from .workflow import Workflow

__all__ = [
    "Artifact",
    "Condition",
    "Finding",
    "Log",
    "LogEntry",
    "Report",
    "Section",
    "Step",
    "Workflow",
]
