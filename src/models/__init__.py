"""Domain models — the workspace's domain entities, one class per file.

The workspace content is the database the harness reads its domain entities from: `Artifact`
⇄ a workspace unit, `Log` ⇄ a run log, `Section` ⇄ a markdown section. `Finding` /
`Report` are the result entities. Configuration entities (Workflow, Step, Condition) live in
the `config` package — they are framework configuration, not workspace data. Models depend
only on the `text` kernel — never on mappers, services, config, or the CLI.

Artifact schemas are pure data (raw dicts), not reified classes.
"""

from __future__ import annotations

from .artifact import Artifact
from .finding import Finding
from .log import Log, LogEntry
from .report import Report
from .section import Section

__all__ = [
    "Artifact",
    "Finding",
    "Log",
    "LogEntry",
    "Report",
    "Section",
]
