"""Shared fixtures for configuration package tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from utils import EnvLoader, SchemaValidator, YamlLoader

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = REPO_ROOT / "contracts"

CAPABILITY_KEYS = (
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


def capabilities(positive: bool = True) -> dict[str, float]:
    """Build a contract-valid capability map."""
    return {key: (1.0 if positive and key == "coding" else 0.0) for key in CAPABILITY_KEYS}


@pytest.fixture()
def config_loader():
    """Build a ConfigLoader with real contract registry collaborators."""
    from config import ConfigLoader

    validator = SchemaValidator.compile_contracts(CONTRACTS_ROOT.rglob("*.schema.json"))
    return ConfigLoader(EnvLoader(), YamlLoader(), validator)


@pytest.fixture()
def framework_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a minimal framework tree and isolate FRAMEWORK_* process variables."""
    for key in FRAMEWORK_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    root = tmp_path / "framework"
    workspace = tmp_path / "workspace"
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
                "FRAMEWORK_WORKSPACE_DIR=../workspace",
            )
        ),
        encoding="utf-8",
    )
    return root


def write_yaml(path: Path, data: str) -> Path:
    """Write a YAML fixture under an existing framework tree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


def model_profiles_yaml() -> str:
    """Return a minimal model profile catalog fixture."""
    caps = "\n".join(f"      {key}: {value}" for key, value in capabilities().items())
    return f"""modelProfiles:
  - slug: fast-coder
    costRank: 2
    description: Fast coding model
    note: Cheap enough for iteration
    capabilities:
{caps}
"""


def access_control_yaml() -> str:
    """Return a minimal ACL fixture."""
    return """actors:
  - slug: planner
    roles:
      - author
roles:
  - slug: author
    privileges:
      - artifact: epic
        action: create
      - artifact: epic
        action: update
"""


def workspace_yaml() -> str:
    """Return a workspace tree fixture with ambiguous artifact path patterns."""
    return """nodes:
  - slug: portfolio
    description: Portfolio folder
    children:
      - slug: epics
        description: Epic folder
        children:
          - slug: <item-slug>.md
            description: Epic artifact
            cardinality: 0..*
            artifact: epic
            template: epic
          - slug: <item-slug>.md
            description: Feature artifact sharing a path pattern
            cardinality: 0..*
            artifact: feature
            template: feature
"""


def workflow_yaml(
    slug: str = "planning",
    predecessor: str | None = None,
    second_step_condition: str = "draft",
    positive_capabilities: bool = True,
) -> str:
    """Return a minimal workflow fixture."""
    caps = "\n".join(
        f"      {key}: {value}" for key, value in capabilities(positive_capabilities).items()
    )
    predecessor_block = f"predecessors:\n  - {predecessor}\n" if predecessor else ""
    return f"""slug: {slug}
{predecessor_block}orchestrator: facilitator
skills:
  - orchestrate
instructions:
  - run-workflow
steps:
  - slug: draft
    actor: planner
    artifact: epic
    skills:
      - drafting
    instructions:
      - draft-instructions
    capabilities:
{caps}
  - slug: review
    actor: reviewer
    artifact: epic
    instructions: review-instructions
    capabilities:
{caps}
    conditions:
      - kind: precondition
        slug: draft-complete
        step: {second_step_condition}
      - kind: postcondition
        slug: review-unblocks-draft
        step: draft
      - kind: precondition
        slug: state-ready
        setSelector:
          setQuery: "artifacts['epic']"
        setPredicate: "size(selected) > 0"
"""


def write_standard_configs(root: Path, workflow: str | None = None) -> None:
    """Write all non-env config fixtures used by happy-path loader tests."""
    write_yaml(root / "conf" / "access-control-list.conf.yaml", access_control_yaml())
    write_yaml(root / "conf" / "model-profiles.conf.yaml", model_profiles_yaml())
    write_yaml(root / "conf" / "workspace.conf.yaml", workspace_yaml())
    write_yaml(root / "conf" / "workflows" / "planning.workflow.conf.yaml", workflow or workflow_yaml())
