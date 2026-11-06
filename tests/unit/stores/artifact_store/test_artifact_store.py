"""Unit tests for the artifact store package."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from config.workspace_layout import WorkspaceLayout
from errors import StateError
from stores.artifact_store import Artifact, ArtifactStore, Commit, Finding, StepRef
from stores.session_log_store import Context, LogEntry, Outcome, Report, SessionLogStore


def _run_git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _artifact_node(slug: str, artifact: str) -> ArtifactNode:
    return ArtifactNode(
        slug=slug,
        description=f"One {artifact}",
        cardinality="0..*",
        artifact=artifact,
        template=artifact,
    )


def _folder_node(slug: str, *children: ArtifactNode | FolderNode) -> FolderNode:
    return FolderNode(slug=slug, description=f"{slug} folder", children=children)


@pytest.fixture()
def artifact_schema(tmp_path: Path) -> Path:
    schema_path = tmp_path / "sample.artifact.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "gsmarc://tests/contracts/sample-artifact/v1",
                "$ref": "gsmarc://saf/contracts/artifact/v1",
                "properties": {"kind": {"const": "sample"}},
            }
        ),
        encoding="utf-8",
    )
    return schema_path


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    _run_git(tmp_path, "init")
    _run_git(tmp_path, "config", "user.email", "test@example.com")
    _run_git(tmp_path, "config", "user.name", "Test User")
    _run_git(tmp_path, "commit", "--allow-empty", "-m", "baseline")
    return tmp_path


@pytest.fixture()
def store(workspace: Path, artifact_schema: Path) -> ArtifactStore:
    return ArtifactStore(
        workspace,
        {"sample": artifact_schema},
        WorkspaceLayout(
            nodes=(_folder_node("sample", _artifact_node("<name>.json", "sample")),)
        ),
    )


def _step_ref(store: ArtifactStore, session_id: str = "sess-1") -> StepRef:
    """Create a step instance's step ref from the workspace branch tip."""
    return store.create_step_ref("flow-01AB", "draft", session_id)


def _tip(workspace: Path, step_ref: StepRef) -> str:
    return _run_git(workspace, "rev-parse", step_ref.name)


class TestArtifact:
    def test_exposes_frozen_artifact_fields(self) -> None:
        artifact = Artifact(ref="sample/a.json", kind="sample", data={"slug": "a"})

        assert artifact.ref == "sample/a.json"
        assert artifact.kind == "sample"
        assert artifact.data["slug"] == "a"
        with pytest.raises(Exception):
            artifact.ref = "changed"  # type: ignore[misc]


class TestFinding:
    def test_exposes_frozen_finding_fields(self) -> None:
        finding = Finding(source="sample/a.json", rule="schema", message="bad")

        assert (finding.source, finding.rule, finding.message) == (
            "sample/a.json",
            "schema",
            "bad",
        )
        with pytest.raises(Exception):
            finding.message = "changed"  # type: ignore[misc]


class TestArtifactStore:
    def test_staging_baseline_cases(self, workspace: Path, store: ArtifactStore) -> None:
        assert store.is_staging_clean(Path("sample/new.json")) is True

        tracked = workspace / "sample" / "tracked.json"
        tracked.parent.mkdir()
        tracked.write_text('{"slug":"tracked","kind":"sample"}\n', encoding="utf-8")
        _run_git(workspace, "add", "sample/tracked.json")
        _run_git(workspace, "commit", "-m", "add tracked")
        assert store.is_staging_clean(Path("sample/tracked.json")) is True

        tracked.write_text('{"slug":"dirty","kind":"sample"}\n', encoding="utf-8")
        assert store.is_staging_clean(Path("sample/tracked.json")) is False

        untracked = workspace / "sample" / "untracked.json"
        untracked.write_text('{"slug":"untracked","kind":"sample"}\n', encoding="utf-8")
        assert store.is_staging_clean(Path("sample/untracked.json")) is False

    def test_commit_artifacts_attributes_session_and_uses_one_commit(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        (workspace / "sample").mkdir()
        (workspace / "sample" / "a.json").write_text(
            '{"slug":"a","kind":"sample"}\n', encoding="utf-8"
        )
        (workspace / "sample" / "b.json").write_text(
            '{"slug":"b","kind":"sample"}\n', encoding="utf-8"
        )
        head = _run_git(workspace, "rev-parse", "HEAD")
        step_ref = _step_ref(store)

        commit = store.commit_artifacts(
            (Path("sample/a.json"), Path("sample/b.json")),
            session_id="sess-1",
            step_ref=step_ref,
        )

        assert commit == Commit(
            commit_id=_tip(workspace, step_ref),
            paths=("sample/a.json", "sample/b.json"),
        )
        assert _run_git(workspace, "rev-list", "--count", f"HEAD..{step_ref.name}") == "1"
        assert "session-id: sess-1" in _run_git(
            workspace, "log", "-1", "--pretty=%B", step_ref.name
        )
        assert _run_git(workspace, "rev-parse", "HEAD") == head
        assert _run_git(workspace, "diff", "--cached", "--name-only") == ""

    def test_commit_artifacts_commits_only_the_validated_set_not_foreign_staged_content(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Function 9, invariant 3: "1 validated set = 1 commit" — the commit carries
        exactly the validated set. Content someone else staged in the index is not part
        of that set: it must stay staged and must not enter the commit."""
        (workspace / "foreign.txt").write_text("not an artifact\n", encoding="utf-8")
        _run_git(workspace, "add", "foreign.txt")
        (workspace / "sample").mkdir()
        (workspace / "sample" / "a.json").write_text(
            '{"slug":"a","kind":"sample"}\n', encoding="utf-8"
        )

        step_ref = _step_ref(store)

        store.commit_artifacts((Path("sample/a.json"),), session_id="sess-1", step_ref=step_ref)

        committed = _run_git(workspace, "show", "--name-only", "--pretty=", step_ref.name)
        assert committed.splitlines() == ["sample/a.json"]
        assert _run_git(workspace, "diff", "--cached", "--name-only") == "foreign.txt"

    def test_commit_artifacts_creates_no_commit_for_a_byte_identical_set(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Function 9, invariant 4: a staged path byte-identical to the step ref tip
        "validates vacuously and stages nothing: a set of only such paths reports
        `valid` and creates no commit"."""
        same = workspace / "sample" / "same.json"
        same.parent.mkdir()
        same.write_text('{"slug":"same","kind":"sample"}\n', encoding="utf-8")
        _run_git(workspace, "add", "sample/same.json")
        _run_git(workspace, "commit", "-m", "add same")
        step_ref = _step_ref(store, "sess-2")

        commit = store.commit_artifacts(
            (Path("sample/same.json"),), session_id="sess-2", step_ref=step_ref
        )

        assert commit is None
        assert _tip(workspace, step_ref) == _run_git(workspace, "rev-parse", "HEAD")

    def test_a_session_log_never_enters_the_committed_workspace_state(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Spec (C0): logs are local-only — never committed or synced.

        Persisted logs sit inside the workspace repository, so the only thing keeping them
        out of workspace state is that the commit gate never stages them. A log written
        beside the artifacts being committed must still be absent from `HEAD` afterwards.
        """
        log_store = SessionLogStore(workspace)
        log_store.create_session_log(
            LogEntry(
                timestamp="2026-08-21T09:00:00.000000Z",
                report=Report(
                    context=Context(function="start-session", session_id="sess-log"),
                    outcome=Outcome(status="started"),
                    payload={
                        "session": {
                            "agent": "orchestrator",
                            "sessionId": "sess-log",
                            "parentSessionId": None,
                        }
                    },
                ),
            )
        )
        (workspace / "sample").mkdir()
        (workspace / "sample" / "a.json").write_text(
            '{"slug":"a","kind":"sample"}\n', encoding="utf-8"
        )

        step_ref = _step_ref(store, "sess-log")
        store.commit_artifacts(
            (Path("sample/a.json"),), session_id="sess-log", step_ref=step_ref
        )

        assert (workspace / "logs" / "sess-log.log.jsonl").is_file()
        committed = _run_git(
            workspace, "ls-tree", "-r", "--name-only", step_ref.name
        ).splitlines()
        assert "sample/a.json" in committed
        assert not [path for path in committed if path.startswith("logs/")]
        assert "logs/" not in _run_git(workspace, "status", "--porcelain", "--untracked-files=all")

    def test_revert_artifact_restores_tracked_path(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        tracked = workspace / "sample" / "tracked.json"
        tracked.parent.mkdir()
        tracked.write_text('{"slug":"tracked","kind":"sample"}\n', encoding="utf-8")
        _run_git(workspace, "add", "sample/tracked.json")
        _run_git(workspace, "commit", "-m", "add tracked")
        tracked.write_text('{"slug":"changed","kind":"sample"}\n', encoding="utf-8")

        finding = store.revert_artifact(Path("sample/tracked.json"))

        assert finding.rule == "restored"
        assert json.loads(tracked.read_text(encoding="utf-8"))["slug"] == "tracked"

    def test_revert_artifact_deletes_new_path(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        new_path = workspace / "sample" / "new.json"
        new_path.parent.mkdir()
        new_path.write_text('{"slug":"new","kind":"sample"}\n', encoding="utf-8")
        _run_git(workspace, "add", "sample/new.json")

        finding = store.revert_artifact(Path("sample/new.json"))

        assert finding.rule == "deleted"
        assert not new_path.exists()
        assert _run_git(workspace, "status", "--short", "--", "sample/new.json") == ""

    def test_validate_artifact_returns_findings_for_invalid_staged_artifact(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        invalid = workspace / "sample" / "invalid.json"
        invalid.parent.mkdir()
        invalid.write_text('{"kind":"sample"}\n', encoding="utf-8")

        findings = store.validate_artifact(Path("sample/invalid.json"))

        assert findings
        assert findings[0].source == "sample/invalid.json"
        assert findings[0].rule == "schema"

    def test_validate_artifact_reports_unparsable_staged_bytes_as_a_finding(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """C6: an invalid staged write is "denied with the schema reports so the agent
        retries" — unparsable agent-authored bytes are a finding for function 9 to
        render, never a system error of the execution environment."""
        broken = workspace / "sample" / "broken.json"
        broken.parent.mkdir()
        broken.write_text("{not json", encoding="utf-8")

        findings = store.validate_artifact(Path("sample/broken.json"))

        assert len(findings) == 1
        assert findings[0].source == "sample/broken.json"
        assert findings[0].rule == "parse"

    def test_validate_artifact_refuses_a_path_no_artifact_schema_resolves(
        self, store: ArtifactStore
    ) -> None:
        """Function 9, precondition (E): "every written path resolves to one of them
        ... violation: `state-error` (`artifact-schema-unresolved`)"."""
        with pytest.raises(StateError) as raised:
            store.validate_artifact(Path("foreign/other.json"))

        assert raised.value.code == "artifact-schema-unresolved"

    def test_discover_yields_valid_committed_artifacts(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        valid = workspace / "sample" / "valid.json"
        valid.parent.mkdir()
        valid.write_text('{"slug":"valid","kind":"sample"}\n', encoding="utf-8")
        _run_git(workspace, "add", "sample/valid.json")
        _run_git(workspace, "commit", "-m", "add valid")

        artifacts = store.discover_artifacts("sample")

        assert artifacts == (
            Artifact(ref="sample/valid.json", kind="sample", data={"slug": "valid", "kind": "sample"}),
        )

    def test_discover_raises_on_invalid_committed_artifact_but_scan_raw_enumerates(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        (workspace / "sample").mkdir()
        (workspace / "sample" / "valid.json").write_text(
            '{"slug":"valid","kind":"sample"}\n', encoding="utf-8"
        )
        (workspace / "sample" / "invalid.json").write_text(
            '{"kind":"sample"}\n', encoding="utf-8"
        )
        _run_git(workspace, "add", "sample/valid.json", "sample/invalid.json")
        _run_git(workspace, "commit", "-m", "foreign invalid")

        assert tuple(store.scan_raw_paths(Path("sample"))) == (
            Path("sample/invalid.json"),
            Path("sample/valid.json"),
        )
        with pytest.raises(StateError):
            store.discover_artifacts("sample")


_FEATURE_REF = "portfolio/payments/features/refunds.feature.json"
_EPIC_REF = "portfolio/portfolio-backlog/payments/payments.epic.md"

# The layout a real framework declares: nested folders whose segments carry `<name>`
# placeholders, so no artifact slug is ever a prefix of the path it is bound to.
_PORTFOLIO_LAYOUT = WorkspaceLayout(
    nodes=(
        _folder_node(
            "portfolio",
            _folder_node(
                "portfolio-backlog",
                _folder_node(
                    "<epic-slug>", _artifact_node("<epic-slug>.epic.md", "epic")
                ),
            ),
            _folder_node(
                "<program-slug>",
                _folder_node(
                    "features",
                    _artifact_node("<feature-slug>.feature.json", "feature"),
                ),
            ),
        ),
    )
)


def _write_schema(directory: Path, slug: str, status_values: list[str]) -> Path:
    """Write one artifact schema extending the harness base contract via `$ref`."""
    schema_path = directory / f"{slug}.artifact.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": f"gsmarc://tests/contracts/{slug}-artifact/v1",
                "$ref": "gsmarc://saf/contracts/artifact/v1",
                "properties": {"status": {"enum": status_values}},
                "required": ["slug", "status"],
            }
        ),
        encoding="utf-8",
    )
    return schema_path


@pytest.fixture()
def portfolio_store(workspace: Path, tmp_path: Path) -> ArtifactStore:
    """Build the store over a realistic nested layout with two artifact kinds."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    return ArtifactStore(
        workspace,
        {
            "epic": _write_schema(schemas_dir, "epic", ["draft", "approved"]),
            "feature": _write_schema(schemas_dir, "feature", ["draft", "approved"]),
        },
        _PORTFOLIO_LAYOUT,
    )


def _stage(workspace: Path, ref: str, content: str) -> Path:
    path = workspace / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestArtifactStoreOverARealisticLayout:
    """The write plane on the layout a framework actually declares."""

    def test_matches_the_artifact_schema_through_the_workspace_layouts_path_patterns(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Spec (function 9, invariant 1): "Every artifact write is validated against its
        matched artifact schema (path patterns; schemas extend the harness base contract
        via `$ref`)"; function 8, invariant 2: the resource is "resolved from the write
        path — via the workspace layout's singleton map ... else via the artifact schemas'
        own path patterns". A nested layout binds `feature` to a path no artifact slug
        prefixes."""
        _stage(workspace, _FEATURE_REF, '{"slug":"refunds","status":"draft"}\n')

        assert portfolio_store.validate_artifact(Path(_FEATURE_REF)) == ()

    def test_reports_the_matched_schemas_violation_on_a_nested_path(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Spec (function 9, invariant 1): the matched schema is the one the LAYOUT binds
        to the path — matching the wrong one, or none, would let an invalid write through
        or refuse a legitimate one."""
        _stage(workspace, _FEATURE_REF, '{"slug":"refunds","status":"shipped"}\n')

        findings = portfolio_store.validate_artifact(Path(_FEATURE_REF))

        assert len(findings) == 1
        assert findings[0].rule == "schema"
        assert "shipped" in findings[0].message

    def test_refuses_a_nested_path_the_layout_binds_to_no_artifact(
        self, portfolio_store: ArtifactStore
    ) -> None:
        """Spec (function 9, precondition (E)): "every written path resolves to one of
        them ... violation: `state-error` (`artifact-schema-unresolved`)" — the layout
        answers unresolvable, and the store surfaces it as that state error."""
        with pytest.raises(StateError) as raised:
            portfolio_store.validate_artifact(Path("portfolio/payments/notes.feature.json"))

        assert raised.value.code == "artifact-schema-unresolved"

    def test_validates_the_frontmatter_of_a_markdown_artifact(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Spec (Classes, `utils`): the loaders include "`MarkdownLoader` (frontmatter +
        body)", and function 9's worked example validates
        `portfolio/payments/features/feature-refunds.md` — a markdown artifact's schema
        binds its FRONTMATTER, so a valid one produces no finding."""
        _stage(
            workspace,
            _EPIC_REF,
            "---\nslug: payments\nstatus: draft\n---\n\n# Payments\n\nBody prose.\n",
        )

        assert portfolio_store.validate_artifact(Path(_EPIC_REF)) == ()

    def test_reports_a_markdown_artifacts_frontmatter_violation_as_a_schema_finding(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Spec (function 9, worked example): the failing markdown artifact's record
        reads `"frontmatter.status: 'shipped' is not one of the enum values"` — a schema
        finding over the frontmatter, never a parse failure over the whole file."""
        _stage(
            workspace,
            _EPIC_REF,
            "---\nslug: payments\nstatus: shipped\n---\n\n# Payments\n",
        )

        findings = portfolio_store.validate_artifact(Path(_EPIC_REF))

        assert len(findings) == 1
        assert findings[0].rule == "schema"
        assert "shipped" in findings[0].message

    def test_discovers_a_committed_markdown_artifact_as_its_frontmatter(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Spec (C6): `discover_artifacts` reads COMMITTED state — the artifact data the
        condition machinery evaluates is the markdown artifact's frontmatter mapping."""
        _stage(
            workspace,
            _EPIC_REF,
            "---\nslug: payments\nstatus: approved\n---\n\n# Payments\n",
        )
        _run_git(workspace, "add", "--", _EPIC_REF)
        _run_git(workspace, "commit", "-m", "add epic")

        artifacts = portfolio_store.discover_artifacts("portfolio/portfolio-backlog")

        assert artifacts == (
            Artifact(
                ref=_EPIC_REF,
                kind="epic",
                data={"slug": "payments", "status": "approved"},
            ),
        )


class TestDiscoverArtifactsByKind:
    """Function 5, invariant 2: the `artifacts` runtime constant is keyed by KIND."""

    def test_reads_every_committed_instance_the_layout_binds_to_the_kind(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        """Kind membership is the layout's answer per committed path — never a path
        prefix — and paths the layout binds to nothing (`.gitignore`, `logs/`) are
        instances of no kind."""
        _stage(workspace, _EPIC_REF, "---\nslug: payments\nstatus: draft\n---\n\n# Payments\n")
        _stage(workspace, _FEATURE_REF, '{"slug":"refunds","status":"draft"}\n')
        _stage(workspace, "logs/01jx.log.jsonl", "{}\n")
        _stage(workspace, ".gitignore", "tmp/\n")
        _run_git(workspace, "add", "--", ".")
        _run_git(workspace, "commit", "-m", "seed")

        epics = portfolio_store.discover_artifacts_by_kind("epic")
        features = portfolio_store.discover_artifacts_by_kind("feature")

        assert [(artifact.ref, artifact.kind) for artifact in epics] == [(_EPIC_REF, "epic")]
        assert [artifact.ref for artifact in features] == [_FEATURE_REF]
        assert portfolio_store.discover_artifacts("epic") == ()

    def test_refuses_a_kind_no_schema_declares(self, portfolio_store: ArtifactStore) -> None:
        with pytest.raises(StateError) as raised:
            portfolio_store.discover_artifacts_by_kind("story")

        assert raised.value.code == "artifact-schema-unresolved"


_MANIFEST_REF = "portfolio/portfolio-manifest.yaml"


@pytest.fixture()
def yaml_store(workspace: Path, tmp_path: Path) -> ArtifactStore:
    """A `.yaml` artifact whose schema types a date as a string, as the org's manifest does."""
    schema_path = tmp_path / "portfolio-manifest.artifact.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "gsmarc://tests/contracts/portfolio-manifest-artifact/v1",
                "$ref": "gsmarc://saf/contracts/artifact/v1",
                "properties": {
                    "slug": {"type": "string"},
                    "stepOutcomes": {
                        "type": "array",
                        "items": {"type": "object", "properties": {"date": {"type": "string"}}},
                    },
                },
                "required": ["slug"],
            }
        ),
        encoding="utf-8",
    )
    layout = WorkspaceLayout(
        nodes=(
            _folder_node(
                "portfolio",
                _artifact_node("portfolio-manifest.yaml", "portfolio-manifest"),
                _artifact_node("<name>.yml", "portfolio-manifest"),
                _artifact_node("<name>.json", "portfolio-manifest"),
            ),
        )
    )
    return ArtifactStore(workspace, {"portfolio-manifest": schema_path}, layout)


_MANIFEST = "slug: acme\nstepOutcomes:\n  - step: sync\n    date: 2026-11-04\n"


class TestYamlArtifacts:
    """The format is the one the artifact's own file extension declares (`.yaml`/`.yml`)."""

    @pytest.mark.parametrize("ref", [_MANIFEST_REF, "portfolio/other.yml"])
    def test_validates_a_yaml_artifact_as_yaml(
        self, workspace: Path, yaml_store: ArtifactStore, ref: str
    ) -> None:
        """Live trial: `portfolio-manifest.yaml` was parsed as JSON ("Expecting value:
        line 1 column 1 (char 0)"); an unquoted date then failed "is not of type 'string'"."""
        _stage(workspace, ref, _MANIFEST)

        assert yaml_store.validate_artifact(Path(ref)) == ()

    def test_discovers_a_committed_yaml_artifact_in_the_json_data_model(
        self, workspace: Path, yaml_store: ArtifactStore
    ) -> None:
        _stage(workspace, _MANIFEST_REF, _MANIFEST)
        _run_git(workspace, "add", "--", _MANIFEST_REF)
        _run_git(workspace, "commit", "-m", "add manifest")

        (artifact,) = yaml_store.discover_artifacts_by_kind("portfolio-manifest")

        assert artifact.data == {
            "slug": "acme", "stepOutcomes": ({"step": "sync", "date": "2026-11-04"},)
        }

    def test_a_json_artifact_is_still_read_as_json(
        self, workspace: Path, yaml_store: ArtifactStore
    ) -> None:
        _stage(workspace, "portfolio/acme.json", "slug: acme\n")

        (finding,) = yaml_store.validate_artifact(Path("portfolio/acme.json"))

        assert finding.rule == "parse"

    @pytest.mark.parametrize(
        ("ref", "content"),
        [(_MANIFEST_REF, "slug: [unclosed\n"), (_EPIC_REF, "---\nslug: [unclosed\n---\n")],
    )
    def test_reports_malformed_yaml_as_a_parse_finding(
        self, workspace: Path, yaml_store: ArtifactStore, portfolio_store: ArtifactStore,
        ref: str, content: str,
    ) -> None:
        store = yaml_store if ref == _MANIFEST_REF else portfolio_store
        _stage(workspace, ref, content)

        (finding,) = store.validate_artifact(Path(ref))

        assert finding.rule == "parse"

    def test_a_markdown_frontmatter_date_stays_a_string(
        self, workspace: Path, portfolio_store: ArtifactStore
    ) -> None:
        _stage(workspace, _EPIC_REF, "---\nslug: payments\nstatus: 2026-11-04\n---\n")

        (finding,) = portfolio_store.validate_artifact(Path(_EPIC_REF))

        assert "'2026-11-04' is not one of" in finding.message
