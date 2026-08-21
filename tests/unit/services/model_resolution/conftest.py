"""Shared fixtures for the model resolution service tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

from config import ModelProfile, ModelProfiles, Step, StepCondition, Workflow, WorkflowCatalog
from stores.session_log_store import Context, LogEntry, Outcome, Report, SessionLogStore
from utils import EnvLoader, SchemaValidator, YamlLoader
from utils.clock import Clock
from utils.jsonl_store import JsonlStore

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACTS_ROOT = REPO_ROOT / "contracts"

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
FRAMEWORK_ENV_KEYS = (
    "FRAMEWORK_DIR",
    "FRAMEWORK_AGENTS_DIR",
    "FRAMEWORK_ARTIFACTS_DIR",
    "FRAMEWORK_SKILLS_DIR",
    "FRAMEWORK_TEMPLATES_DIR",
    "FRAMEWORK_WORKFLOWS_DIR",
    "FRAMEWORK_INSTRUCTIONS_DIR",
    "FRAMEWORK_WORKSPACE_DIR",
)

ORCHESTRATOR_SESSION = "orchestrator-1"
STEP_SESSION = "step-1"
WORKFLOW_SLUG = "verification"
INSTANCE_ID = "verification-01J9XQ"
STEP_SLUG = "review"
STEP_ACTOR = "qa-engineer"


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
    slug: str = STEP_SLUG,
    actor: str = STEP_ACTOR,
    weights: Mapping[str, float] | None = None,
    predecessors: Sequence[str] = (),
) -> Step:
    """Build one configured step carrying its weighted capability demand."""
    return Step(
        slug=slug,
        actor=actor,
        artifact="review-report",
        instructions=("review",),
        capabilities=weights or build_capabilities(deep_reasoning=9.0),
        skills=("code-review",),
        conditions=tuple(
            StepCondition(kind="precondition", slug=f"after-{predecessor}", step=predecessor)
            for predecessor in predecessors
        ),
    )


def build_catalog(*steps: Step, slug: str = WORKFLOW_SLUG) -> WorkflowCatalog:
    """Build a single-workflow catalog over the given authored step order."""
    workflow = Workflow(slug=slug, facilitator="workflow-orchestrator", steps=tuple(steps))
    return WorkflowCatalog(workflows={slug: workflow})


def build_profiles(*specs: tuple[str, int, Mapping[str, float]]) -> ModelProfiles:
    """Build a model catalog from `(slug, costRank, capabilities)` specs."""
    return ModelProfiles(
        profiles={
            slug: ModelProfile(slug=slug, cost_rank=cost_rank, capabilities=dict(capabilities))
            for slug, cost_rank, capabilities in specs
        }
    )


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


def start_session_log(
    store: SessionLogStore,
    session_id: str = ORCHESTRATOR_SESSION,
    agent: str = "workflow-orchestrator",
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
    timestamp: str = "2026-08-17T13:01:00Z",
    step_slug: str = STEP_SLUG,
    session_id: str = ORCHESTRATOR_SESSION,
    workflow_instance_id: str | None = INSTANCE_ID,
    actor: str = STEP_ACTOR,
    weights: Mapping[str, float] | None = None,
) -> None:
    """Journal a function 3 step resolution into the invoking session's log."""
    store.append_log_entry(
        session_id,
        build_entry(
            timestamp,
            "resolve-step",
            "step-resolution",
            session_id,
            workflow_instance_id,
            payload={
                "step": {
                    "slug": step_slug,
                    "actor": actor,
                    "artifact": "review-report",
                    "instructions": ["review"],
                    "capabilities": dict(weights or build_capabilities(deep_reasoning=9.0)),
                }
            },
        ),
    )


def append_outcome(
    store: SessionLogStore,
    timestamp: str = "2026-08-17T13:02:00Z",
    status: str = "pass",
    session_id: str = ORCHESTRATOR_SESSION,
    workflow_instance_id: str = INSTANCE_ID,
) -> None:
    """Journal a function 10 postcondition outcome, concluding the in-flight step.

    Spec (function 10, Postconditions): the outcome entry is "appended to the
    dispatching (orchestrator) session's log" — the same log the resolution it
    concludes lives in, never the step session's.
    """
    store.append_log_entry(
        session_id,
        build_entry(
            timestamp,
            "check-step-postconditions",
            status,
            session_id,
            workflow_instance_id,
            payload={"conditionChecks": []},
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


def write_unroutable_workflow(framework_root: Path) -> None:
    """Author a workflow whose only step weights every capability tag zero."""
    weights = "\n".join(f"      {tag}: 0" for tag in CAPABILITY_TAGS)
    (framework_root / "conf" / "workflows" / f"{WORKFLOW_SLUG}.workflow.conf.yaml").write_text(
        f"""slug: {WORKFLOW_SLUG}
orchestrator: workflow-orchestrator
steps:
  - slug: {STEP_SLUG}
    actor: {STEP_ACTOR}
    artifact: review-report
    instructions:
      - review
    capabilities:
{weights}
""",
        encoding="utf-8",
    )


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


@pytest.fixture()
def config_loader():
    """Build a ConfigLoader with real contract registry collaborators."""
    from config import ConfigLoader

    validator = SchemaValidator.compile_contracts(CONTRACTS_ROOT.rglob("*.schema.json"))
    return ConfigLoader(EnvLoader(), YamlLoader(), validator)


@pytest.fixture()
def framework_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a minimal framework tree and isolate the FRAMEWORK_* process variables."""
    for key in FRAMEWORK_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    root = tmp_path / "framework"
    workspace = tmp_path / "framework-workspace"
    for path in (
        root / "agents",
        root / "artifacts",
        root / "skills",
        root / "templates",
        root / "conf" / "workflows",
        root / "instructions",
        workspace,
    ):
        path.mkdir(parents=True)

    (root / ".env").write_text(
        "\n".join(
            (
                "FRAMEWORK_AGENTS_DIR=agents",
                "FRAMEWORK_ARTIFACTS_DIR=artifacts",
                "FRAMEWORK_SKILLS_DIR=skills",
                "FRAMEWORK_TEMPLATES_DIR=templates",
                "FRAMEWORK_WORKFLOWS_DIR=conf/workflows",
                "FRAMEWORK_INSTRUCTIONS_DIR=instructions",
                "FRAMEWORK_WORKSPACE_DIR=../framework-workspace",
            )
        ),
        encoding="utf-8",
    )
    return root
