"""Regression tests: the CEL `artifacts` map is keyed by artifact KIND and carries
harness-owned provenance.

The layout here is a real framework's shape — nested folders whose segments are
`<name>` placeholders — so no artifact slug is ever a path prefix of its instances.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from checking_fixtures import build_instance_view, run_git
from config import StateCondition, WorkspaceLayout
from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from errors import StateError
from services.checking import ConditionEvaluator
from stores.artifact_store import ArtifactStore

EPIC_REF = "portfolio/portfolio-backlog/probe-epic/probe-epic.epic.md"
FEATURE_REF = "portfolio/probe-epic/features/refunds.feature.json"
STEP_SESSION = "01jstepsession"


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


def _commit(workspace: Path, ref: str, content: str, session_id: str | None = None) -> None:
    path = workspace / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    message = f"seed {ref}"
    if session_id is not None:
        message = f"artifact: commit validated write set\n\nsession-id: {session_id}"
    run_git(workspace, "add", "--", ref)
    run_git(workspace, "commit", "-m", message)


def _epic(slug: str, status: str = "draft", extra: str = "") -> str:
    return f"---\nslug: {slug}\nstatus: {status}\n{extra}---\n\n# {slug}\n"


def _evaluate(
    store: ArtifactStore,
    set_query: str,
    set_predicate: str,
    artifact: str = "epic",
    step_session_ids: tuple[str, ...] = (),
) -> str:
    condition = StateCondition(
        kind="postcondition",
        slug="probe",
        set_selector={"setQuery": set_query},
        set_predicate=set_predicate,
    )
    [check] = ConditionEvaluator(store).evaluate_conditions(
        (condition,), build_instance_view([]), artifact, step_session_ids
    )
    return check.outcome


class TestArtifactsByKind:
    def test_resolves_instances_by_kind_through_the_workspace_layout(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: `artifacts` maps an artifact (schema) slug to its
        instances — an Epic committed under `portfolio/portfolio-backlog/...` IS in
        `artifacts['epic']`, though no folder is named `epic`."""
        _commit(workspace, EPIC_REF, _epic("probe-epic"))
        _commit(workspace, FEATURE_REF, json.dumps({"slug": "refunds"}))

        assert _evaluate(
            portfolio_store,
            "artifacts['epic']",
            "selected.size() == 1 && selected[0].slug == 'probe-epic'",
        ) == "pass"
        assert _evaluate(
            portfolio_store, "artifacts['feature']", "selected.size() == 1"
        ) == "pass"

    def test_indexes_by_the_declared_artifact_constant(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: `artifacts[artifact]` is the step's declared kind."""
        _commit(workspace, EPIC_REF, _epic("probe-epic"))

        assert _evaluate(
            portfolio_store, "artifacts[artifact]", "selected.size() == 1", "epic"
        ) == "pass"
        assert _evaluate(
            portfolio_store, "artifacts[artifact]", "selected.size() == 0", "feature"
        ) == "pass"


class TestArtifactProvenance:
    def test_exposes_the_committing_session_as_harness_owned_provenance(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Artifact provenance: `provenance.sessionId` is the session of the latest
        harness commit on the instance's path; a user-seeded commit attributes none."""
        _commit(workspace, EPIC_REF, _epic("probe-epic"), session_id=STEP_SESSION)
        _commit(
            workspace,
            "portfolio/portfolio-backlog/seeded/seeded.epic.md",
            _epic("seeded"),
        )

        assert _evaluate(
            portfolio_store,
            "artifacts['epic'].filter(a, a.provenance.sessionId == '01jstepsession')",
            "selected.size() == 1 && selected[0].slug == 'probe-epic'",
        ) == "pass"
        assert _evaluate(
            portfolio_store,
            "artifacts['epic'].filter(a, a.provenance.sessionId == null)",
            "selected.size() == 1 && selected[0].slug == 'seeded'",
        ) == "pass"

    def test_provenance_follows_the_latest_commit_on_the_path(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Artifact provenance: an update re-attributes the instance to the updating
        session — how an update-type step proves it touched its artifact."""
        _commit(workspace, EPIC_REF, _epic("probe-epic"), session_id="01jdrafter")
        _commit(workspace, EPIC_REF, _epic("probe-epic", "approved"), session_id=STEP_SESSION)

        assert _evaluate(
            portfolio_store,
            "artifacts[artifact].filter(a, a.provenance.sessionId in stepSessionIds)",
            "selected.size() == 1",
            step_session_ids=(STEP_SESSION,),
        ) == "pass"

    def test_step_session_ids_is_empty_by_default(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Function 5, invariant 2: `stepSessionIds` is always in scope — empty before
        any step session is registered — and a null provenance never matches it."""
        _commit(workspace, EPIC_REF, _epic("probe-epic"))

        assert _evaluate(
            portfolio_store,
            "artifacts[artifact].filter(a, a.provenance.sessionId in stepSessionIds)",
            "selected.size() == 0 && stepSessionIds.size() == 0",
        ) == "pass"

    def test_an_artifact_supplying_provenance_cannot_forge_it(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Artifact provenance: harness-owned — an instance whose own data supplies
        `provenance` is invalid, at the write gate (function 9) and when committed
        state is read (C6), so no artifact can claim a session's authorship."""
        forged = _epic("probe-epic", extra=f"provenance:\n  sessionId: {STEP_SESSION}\n")
        _commit(workspace, EPIC_REF, forged)

        findings = portfolio_store.validate_artifact(Path(EPIC_REF))
        assert [finding.rule for finding in findings] == ["reserved"]
        with pytest.raises(StateError) as raised:
            _evaluate(
                portfolio_store,
                "artifacts['epic'].filter(a, a.provenance.sessionId in stepSessionIds)",
                "selected.size() > 0",
                step_session_ids=(STEP_SESSION,),
            )
        assert raised.value.code == "artifact-invalid"
