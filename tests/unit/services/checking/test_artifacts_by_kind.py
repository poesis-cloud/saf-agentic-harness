"""Regression tests: the CEL `artifacts` map is keyed by artifact KIND.

The layout here is a real framework's shape — nested folders whose segments are
`<name>` placeholders — so no artifact slug is ever a path prefix of its instances.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from checking_fixtures import build_instance_view, build_scope, run_git
from config import StateCondition, WorkspaceLayout
from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from services.checking import ConditionEvaluator
from stores.artifact_store import ArtifactStore

EPIC_REF = "portfolio/portfolio-backlog/probe-epic/probe-epic.epic.md"
FEATURE_REF = "portfolio/probe-epic/features/refunds.feature.json"


def _node(slug: str, artifact: str) -> ArtifactNode:
    return ArtifactNode(
        slug=slug,
        description=f"One {artifact}",
        cardinality="0..*",
        artifact=artifact,
        template=artifact,
    )


def _folder(slug: str, *children: ArtifactNode | FolderNode) -> FolderNode:
    return FolderNode(slug=slug, description=f"{slug} folder", children=children)


_LAYOUT = WorkspaceLayout(
    nodes=(
        _folder(
            "portfolio",
            _folder(
                "portfolio-backlog",
                _folder("<epic-slug>", _node("<epic-slug>.epic.md", "epic")),
            ),
            _folder(
                "<epic-slug>",
                _folder("features", _node("<feature-slug>.feature.json", "feature")),
            ),
        ),
        _folder("logs"),
    )
)


@pytest.fixture()
def portfolio_store(tmp_path: Path, workspace: Path) -> ArtifactStore:
    schemas = {}
    for kind in ("epic", "feature"):
        path = tmp_path / f"{kind}.artifact.schema.json"
        path.write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": f"gsmarc://tests/contracts/{kind}-artifact/v1",
                    "$ref": "gsmarc://saf/contracts/artifact/v1",
                    "properties": {"status": {"type": "string"}},
                }
            ),
            encoding="utf-8",
        )
        schemas[kind] = path
    return ArtifactStore(workspace, schemas, _LAYOUT)


def _commit(workspace: Path, ref: str, content: str) -> None:
    path = workspace / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    run_git(workspace, "add", "--", ref)
    run_git(workspace, "commit", "-m", f"seed {ref}")


def _epic(slug: str, status: str = "draft") -> str:
    return f"---\nslug: {slug}\nstatus: {status}\n---\n\n# {slug}\n"


def _evaluate(
    store: ArtifactStore,
    set_query: str,
    set_predicate: str,
    artifact: str = "epic",
) -> str:
    condition = StateCondition(
        kind="postcondition",
        slug="probe",
        set_selector={"setQuery": set_query},
        set_predicate=set_predicate,
    )
    [check] = ConditionEvaluator(store).evaluate_conditions(
        (condition,), build_instance_view([]), build_scope()
    )
    return check.outcome


class TestArtifactsByKind:
    def test_resolves_instances_by_kind_through_the_workspace_layout(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: `a.kind` is the schema slug, from the workspace
        layout — an Epic committed under `portfolio/portfolio-backlog/...` IS of kind
        `epic`, though no folder is named `epic`."""
        _commit(workspace, EPIC_REF, _epic("probe-epic"))
        _commit(workspace, FEATURE_REF, json.dumps({"slug": "refunds"}))

        assert _evaluate(
            portfolio_store,
            "workspace.filter(a, a.kind == 'epic')",
            "selected.size() == 1 && selected[0].data.slug == 'probe-epic'"
            f" && selected[0].path == '{EPIC_REF}'",
        ) == "pass"
        assert _evaluate(
            portfolio_store, "workspace.filter(a, a.kind == 'feature')", "selected.size() == 1"
        ) == "pass"

    def test_a_content_check_reads_instances_outside_a_slug_named_folder(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: a content postcondition guarded on kind `epic`
        sees the committed Epic's `data` though no folder is named `epic`."""
        _commit(workspace, EPIC_REF, _epic("probe-epic", status="approved"))

        assert _evaluate(
            portfolio_store,
            "workspace.filter(a, a.kind == 'epic' && a.data.status == 'approved')",
            "selected.size() > 0",
        ) == "pass"
        assert _evaluate(
            portfolio_store,
            "workspace.filter(a, a.kind == 'epic' && a.data.status == 'draft')",
            "selected.size() > 0",
        ) == "fail"
