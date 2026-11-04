"""Regression tests: CEL reads YAML artifacts as JSON data.

Live trial: the org's `portfolio/portfolio-manifest.yaml` was parsed with `json.loads`, and an
unquoted `date: 2026-11-04` became a `datetime.date` its JSON Schema refused as "not of type
'string'". Artifact schemas validate JSON data, so the condition data is that data too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from checking_fixtures import build_instance_view, run_git
from config import StateCondition, WorkspaceLayout
from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from services.checking import ConditionEvaluator
from stores.artifact_store import ArtifactStore

MANIFEST_REF = "portfolio/portfolio-manifest.yaml"
EPIC_REF = "portfolio/backlog/probe.epic.md"

_LAYOUT = WorkspaceLayout(
    nodes=(
        FolderNode(
            slug="portfolio",
            description="portfolio",
            children=(
                ArtifactNode(slug="portfolio-manifest.yaml", description="manifest",
                             cardinality="1", artifact="portfolio-manifest",
                             template="portfolio-manifest"),
                FolderNode(slug="backlog", description="backlog", children=(
                    ArtifactNode(slug="<epic-slug>.epic.md", description="epic",
                                 cardinality="0..*", artifact="epic", template="epic"),
                )),
            ),
        ),
    )
)


@pytest.fixture()
def store(tmp_path: Path, workspace: Path) -> ArtifactStore:
    schemas = {}
    for kind in ("portfolio-manifest", "epic"):
        path = tmp_path / f"{kind}.artifact.schema.json"
        path.write_text(json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"gsmarc://tests/contracts/{kind}-artifact/v1",
            "$ref": "gsmarc://saf/contracts/artifact/v1",
            "properties": {
                "date": {"type": "string"},
                "stepOutcomes": {"type": "array", "items": {
                    "type": "object", "properties": {"date": {"type": "string"}}}},
            },
            "required": ["date"],
        }), encoding="utf-8")
        schemas[kind] = path
    return ArtifactStore(workspace, schemas, _LAYOUT)


def _commit(workspace: Path, ref: str, content: str) -> None:
    path = workspace / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    run_git(workspace, "add", "--", ref)
    run_git(workspace, "commit", "-m", f"seed {ref}")


def _evaluate(store: ArtifactStore, set_query: str, set_predicate: str) -> str:
    condition = StateCondition(
        kind="postcondition", slug="probe",
        set_selector={"setQuery": set_query}, set_predicate=set_predicate,
    )
    [check] = ConditionEvaluator(store).evaluate_conditions(
        (condition,), build_instance_view([]), "portfolio-manifest"
    )
    return check.outcome


def test_a_yaml_artifacts_unquoted_dates_reach_cel_as_strings(
    workspace: Path, store: ArtifactStore
) -> None:
    _commit(workspace, MANIFEST_REF,
            "slug: acme\ndate: 2026-11-04\nstepOutcomes:\n  - step: sync\n    date: 2026-11-04 10:00:00\n")

    assert _evaluate(
        store, "artifacts['portfolio-manifest']",
        "selected.size() == 1 && selected[0].date == '2026-11-04'"
        " && selected[0].stepOutcomes[0].date == '2026-11-04 10:00:00'",
    ) == "pass"


def test_a_markdown_artifacts_frontmatter_dates_reach_cel_as_strings(
    workspace: Path, store: ArtifactStore
) -> None:
    _commit(workspace, EPIC_REF, "---\nslug: probe\ndate: 2026-11-04\n---\n\n# Probe\n")

    assert _evaluate(
        store, "artifacts['epic']", "selected.size() == 1 && selected[0].date == '2026-11-04'"
    ) == "pass"
