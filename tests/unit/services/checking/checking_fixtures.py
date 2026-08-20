"""Fixture builders for the checking services' unit tests.

Kept out of `conftest.py` so test modules can import the builders directly while
pytest keeps owning the fixtures themselves.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

from config import (
    StateCondition,
    Step,
    StepCondition,
    Workflow,
    WorkflowCatalog,
)
from stores.session_log_store import (
    Context,
    LogEntry,
    Outcome,
    Report,
    WorkflowInstanceView,
)

INSTANCE_ID = "verification-01J9XQ"
WORKFLOW_SLUG = "verification"
ORCHESTRATOR_SESSION = "01j9xq0f2m"
STEP_SESSION = "01j9xqr7t3"

# The canonical capability-tag vocabulary: every tag is explicit (model-profiles contract).
CAPABILITIES = {
    "deep-reasoning": 8,
    "coding": 2,
    "tool-use": 4,
    "long-context": 5,
    "multimodal": 0,
    "writing-quality": 7,
    "instruction-following": 9,
    "fast-iteration": 3,
    "schema-adherence": 9,
}


def run_git(workspace: Path, *args: str) -> str:
    """Run one Git command in a fixture workspace."""
    result = subprocess.run(
        ("git", *args),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def build_step(
    slug: str = "review",
    *,
    actor: str = "qa-engineer",
    artifact: str = "review-report",
    conditions: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build one contract-valid workflow step object for a log fixture."""
    return {
        "slug": slug,
        "actor": actor,
        "artifact": artifact,
        "instructions": ["reports-handling"],
        "capabilities": dict(CAPABILITIES),
        "conditions": list(conditions),
    }


def build_entry(
    function: str,
    status: str,
    *,
    session_id: str,
    parent_session_id: str | None = None,
    workflow_instance_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
    timestamp: str = "2026-01-01T00:00:00Z",
) -> LogEntry:
    """Build one contract-valid log entry for a fixture session log."""
    return LogEntry(
        timestamp=timestamp,
        report=Report(
            context=Context(
                function=function,
                session_id=session_id,
                parent_session_id=parent_session_id,
                workflow_instance_id=workflow_instance_id,
            ),
            outcome=Outcome(status=status),
            payload=dict(payload or {}),
        ),
    )


def build_registration_entry(
    session_id: str,
    agent: str,
    *,
    parent_session_id: str | None = None,
    workflow_instance_id: str | None = None,
    timestamp: str = "2026-01-01T00:00:00Z",
) -> LogEntry:
    """Build function 0's registration entry — the log's first line."""
    session: dict[str, Any] = {"sessionId": session_id, "agent": agent}
    if parent_session_id is not None:
        session["parentSessionId"] = parent_session_id
    return build_entry(
        "start-session",
        "started",
        session_id=session_id,
        parent_session_id=parent_session_id,
        workflow_instance_id=workflow_instance_id,
        payload={"session": session},
        timestamp=timestamp,
    )


def build_step_resolution_entry(
    session_id: str,
    step: Mapping[str, Any],
    *,
    workflow_instance_id: str = INSTANCE_ID,
    timestamp: str = "2026-01-01T00:01:00Z",
) -> LogEntry:
    """Build function 3's step-resolution entry — what puts a step in flight."""
    return build_entry(
        "resolve-step",
        "step-resolution",
        session_id=session_id,
        workflow_instance_id=workflow_instance_id,
        payload={"step": dict(step)},
        timestamp=timestamp,
    )


def build_ending_entry(
    session_id: str,
    *,
    timestamp: str = "2026-01-01T23:59:00Z",
) -> LogEntry:
    """Build function 11's ending entry — what C8 refuses every later call on."""
    return build_entry(
        "end-session",
        "ended",
        session_id=session_id,
        timestamp=timestamp,
    )


def build_catalog(
    *,
    conditions: Sequence[StepCondition | StateCondition] = (),
    step_slug: str = "review",
    actor: str = "qa-engineer",
    artifact: str = "review-report",
) -> WorkflowCatalog:
    """Build the catalog holding the fixture workflow and its single step."""
    step = Step(
        slug=step_slug,
        actor=actor,
        artifact=artifact,
        instructions=("reports-handling",),
        capabilities=dict(CAPABILITIES),
        conditions=tuple(conditions),
    )
    return WorkflowCatalog(
        workflows={
            WORKFLOW_SLUG: Workflow(
                slug=WORKFLOW_SLUG, facilitator="orchestrator", steps=(step,)
            )
        }
    )


def build_instance_view(entries: Sequence[LogEntry]) -> WorkflowInstanceView:
    """Assemble an instance view straight from entries, without touching disk."""
    return WorkflowInstanceView(
        workflow_instance_id=INSTANCE_ID, entries=tuple(entries)
    )


def read_entries(workspace: Path, session_id: str) -> list[dict[str, Any]]:
    """Read one session log's raw entries straight off disk."""
    path = workspace / "logs" / f"{session_id}.log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_artifact(
    workspace: Path, ref: str, data: Mapping[str, Any], *, commit: bool = True
) -> Path:
    """Write one artifact into the workspace, optionally committing it into `HEAD`."""
    path = workspace / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    if commit:
        run_git(workspace, "add", "--", ref)
        run_git(workspace, "commit", "-m", f"add {ref}")
    return path
