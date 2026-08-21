"""Shared fixtures for the step resolution service tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from config import Step, StepCondition, Workflow, WorkflowCatalog
from stores.session_log_store import Context, LogEntry, Outcome, Report, SessionLogStore
from utils.clock import Clock
from utils.jsonl_store import JsonlStore

CAPABILITY_TAGS = (
    "deep-reasoning",
    "coding",
    "tool-use",
    "long-context",
    "multimodal",
    "writing-quality",
    "instruction-following",
    "fast-iteration",
    "schema-adherence",
)

ORCHESTRATOR_SESSION = "orchestrator-1"
STEP_SESSION = "step-1"
FACILITATOR = "workflow-orchestrator"
WORKFLOW_SLUG = "verification"
INSTANCE_ID = "verification-01J9XQ"


def build_capabilities(**weights: float) -> dict[str, float]:
    """Build a contract-valid capability map, defaulting every tag to zero."""
    capabilities = {tag: 0.0 for tag in CAPABILITY_TAGS}
    capabilities.update({tag.replace("_", "-"): value for tag, value in weights.items()})
    return capabilities


class SequenceClock(Clock):
    """Emit fixed timestamps so journaled entries order deterministically."""

    def __init__(self, *timestamps: str) -> None:
        """Create a clock over the timestamps to emit, repeating the last one."""
        self._timestamps = list(timestamps) or ["2026-08-17T15:00:00Z"]

    def read_timestamp(self) -> str:
        """Emit the next timestamp."""
        if len(self._timestamps) > 1:
            return self._timestamps.pop(0)
        return self._timestamps[0]


class FailingAppendJsonlStore(JsonlStore):
    """Fake a failing environment: loads normally, never manages to append."""

    def append_entry(self, path: str | Path, entry: Mapping[str, Any]) -> None:
        """Fail the append the way a full disk would."""
        raise OSError("No space left on device")


def build_step(
    slug: str,
    actor: str = "qa-engineer",
    artifact: str = "review-report",
    predecessors: Sequence[str] = (),
    successors: Sequence[str] = (),
    weights: Mapping[str, float] | None = None,
    skills: Sequence[str] = ("code-review",),
    instructions: Sequence[str] = ("review",),
) -> Step:
    """Build one configured step with its structural step conditions."""
    conditions: list[StepCondition] = [
        StepCondition(kind="precondition", slug=f"after-{predecessor}", step=predecessor)
        for predecessor in predecessors
    ]
    conditions.extend(
        StepCondition(kind="postcondition", slug=f"unblocks-{successor}", step=successor)
        for successor in successors
    )
    return Step(
        slug=slug,
        actor=actor,
        artifact=artifact,
        instructions=tuple(instructions),
        capabilities=weights or build_capabilities(deep_reasoning=9.0),
        skills=tuple(skills),
        conditions=tuple(conditions),
    )


def build_catalog(
    *steps: Step,
    slug: str = WORKFLOW_SLUG,
    facilitator: str = FACILITATOR,
) -> WorkflowCatalog:
    """Build a single-workflow catalog over the given authored step order."""
    workflow = Workflow(slug=slug, facilitator=facilitator, steps=tuple(steps))
    return WorkflowCatalog(workflows={slug: workflow})


def build_entry(
    timestamp: str,
    function: str,
    status: str,
    session_id: str,
    workflow_instance_id: str | None = None,
    payload: Mapping[str, Any] | None = None,
) -> LogEntry:
    """Build one contract-shaped log entry."""
    return LogEntry(
        timestamp=timestamp,
        report=Report(
            context=Context(
                function=function,
                session_id=session_id,
                parent_session_id=None,
                workflow_instance_id=workflow_instance_id,
            ),
            outcome=Outcome(status=status),
            payload=dict(payload or {}),
        ),
    )


def build_step_payload(
    slug: str,
    actor: str = "qa-engineer",
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Build the journaled step payload a resolution entry carries."""
    return {
        "slug": slug,
        "actor": actor,
        "artifact": "review-report",
        "instructions": ["review"],
        "capabilities": dict(weights or build_capabilities(deep_reasoning=9.0)),
    }


def start_session_log(
    store: SessionLogStore,
    session_id: str = ORCHESTRATOR_SESSION,
    agent: str = FACILITATOR,
    timestamp: str = "2026-08-17T13:00:00Z",
) -> None:
    """Register a session by writing function 0's start entry."""
    store.create_session_log(
        build_entry(
            timestamp,
            "start-session",
            "started",
            session_id,
            payload={
                "session": {
                    "agent": agent,
                    "sessionId": session_id,
                    "parentSessionId": None,
                }
            },
        )
    )


def append_resolution(
    store: SessionLogStore,
    timestamp: str,
    step_slug: str,
    session_id: str = ORCHESTRATOR_SESSION,
    workflow_instance_id: str = INSTANCE_ID,
    actor: str = "qa-engineer",
) -> None:
    """Journal a function 3 step resolution into a session log."""
    store.append_log_entry(
        session_id,
        build_entry(
            timestamp,
            "resolve-step",
            "step-resolution",
            session_id,
            workflow_instance_id,
            payload={"step": build_step_payload(step_slug, actor)},
        ),
    )


def build_condition_checks(status: str) -> list[dict[str, Any]]:
    """Build the condition checks a function 10 outcome carries — a fail names one."""
    if status != "fail":
        return []
    return [
        {
            "condition": {
                "kind": "postcondition",
                "slug": "report-exists",
                "step": "review",
            },
            "outcome": "fail",
            "failureMessage": "The step's postconditions do not hold.",
        }
    ]


def append_outcome(
    store: SessionLogStore,
    timestamp: str,
    status: str = "pass",
    session_id: str = STEP_SESSION,
    workflow_instance_id: str = INSTANCE_ID,
) -> None:
    """Journal a function 10 postcondition outcome into a step session log."""
    store.append_log_entry(
        session_id,
        build_entry(
            timestamp,
            "check-step-postconditions",
            status,
            session_id,
            workflow_instance_id,
            payload={"conditionChecks": build_condition_checks(status)},
        ),
    )


def append_ending(
    store: SessionLogStore,
    session_id: str = ORCHESTRATOR_SESSION,
    timestamp: str = "2026-08-17T14:00:00Z",
) -> None:
    """Journal function 11's ending entry, the entry C8 refuses on."""
    store.append_log_entry(
        session_id,
        build_entry(timestamp, "end-session", "ended", session_id),
    )


def read_entries(workspace_dir: Path, session_id: str) -> tuple[Mapping[str, Any], ...]:
    """Read one session log's raw entries straight from the working tree."""
    path = workspace_dir / "logs" / f"{session_id}.log.jsonl"
    if not path.exists():
        return ()
    return JsonlStore().load_entries(path)


@pytest.fixture()
def workspace_dir(tmp_path: Path) -> Path:
    """Create an isolated workspace directory for one test."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture()
def log_store(workspace_dir: Path) -> SessionLogStore:
    """Build the real session log store over the isolated workspace."""
    return SessionLogStore(workspace_dir)
