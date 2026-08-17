"""Unit tests for the artifact store package."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from errors import StateError
from stores.artifact_store import Artifact, ArtifactStore, Finding


def _run_git(workspace: Path, *args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=workspace,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


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
    return ArtifactStore(workspace, {"sample": artifact_schema})


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
        before = int(_run_git(workspace, "rev-list", "--count", "HEAD"))

        store.commit_artifacts(
            (Path("sample/a.json"), Path("sample/b.json")), session_id="sess-1"
        )

        assert int(_run_git(workspace, "rev-list", "--count", "HEAD")) == before + 1
        assert "sess-1" in _run_git(workspace, "log", "-1", "--pretty=%B")

    def test_commit_artifacts_creates_no_commit_for_a_byte_identical_set(
        self, workspace: Path, store: ArtifactStore
    ) -> None:
        """Function 9, invariant 4: a staged path byte-identical to `HEAD` "validates
        vacuously and stages nothing: a set of only such paths reports `valid` and
        creates no commit"."""
        same = workspace / "sample" / "same.json"
        same.parent.mkdir()
        same.write_text('{"slug":"same","kind":"sample"}\n', encoding="utf-8")
        _run_git(workspace, "add", "sample/same.json")
        _run_git(workspace, "commit", "-m", "add same")
        before = int(_run_git(workspace, "rev-list", "--count", "HEAD"))

        store.commit_artifacts((Path("sample/same.json"),), session_id="sess-2")

        assert int(_run_git(workspace, "rev-list", "--count", "HEAD")) == before

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
