"""Unit tests — SchemaChecker: the reporting surface over schema conformance, catalog
integrity, and native-JSON validation. Uses the synthetic colocated framework tree from the
ArtifactValidator suite."""

from __future__ import annotations

import json

from config import SchemaCatalog
from mappers import Workspace
from models import Artifact
from services import SchemaChecker

from test_artifact_validator import BASE_CONTRACT, _schema


def _tree(tmp_path, schemas: dict[str, dict], templates: dict[str, str] | None = None) -> Workspace:
    (tmp_path / "harness" / "contracts").mkdir(parents=True)
    (tmp_path / "harness" / "contracts" / "artifact.schema.json").write_text(json.dumps(BASE_CONTRACT))
    (tmp_path / "schemas").mkdir()
    for schema_id, doc in schemas.items():
        (tmp_path / "schemas" / f"{schema_id}.artifact.schema.json").write_text(json.dumps(doc))
    for rel, text in (templates or {}).items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp_path / "workspace").mkdir()
    return Workspace(tmp_path, tmp_path / "workspace")


def _checker(ws: Workspace) -> SchemaChecker:
    return SchemaChecker(ws, SchemaCatalog(ws))


class TestSchemaChecker:
    def test_conformance_loops_the_validator_over_targets(self, tmp_path):
        ws = _tree(tmp_path, {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug"])})
        (ws.workspace_root / "backlog").mkdir(parents=True)
        (ws.workspace_root / "backlog/a.epic.md").write_text("---\nslug: a\n---\n")
        (ws.workspace_root / "backlog/b.epic.md").write_text("---\n---\n")
        good = Artifact("epic", ws.workspace_root / "backlog/a.epic.md", {"slug": "a"}, "")
        bad = Artifact("epic", ws.workspace_root / "backlog/b.epic.md", {}, "")
        report = _checker(ws).conformance([good, bad])
        assert report.has_errors()
        assert all("b.epic.md" in f.path for f in report.findings)

    def test_catalog_flags_unclaimed_templates(self, tmp_path):
        ws = _tree(
            tmp_path,
            {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug"])},
            templates={"layers/actors/x/artifacts/orphan.artifact-template.md": "# t"},
        )
        report = _checker(ws).catalog()
        assert any("no registry artifact schema declares this template" in f.message for f in report.findings)

    def test_catalog_accepts_claimed_templates(self, tmp_path):
        schema = _schema("epic", ["backlog/*.epic.md"], ["slug"])
        schema["x-artifact"]["template"] = "layers/actors/x/artifacts/epic.artifact-template.md"
        ws = _tree(tmp_path, {"epic": schema},
                   templates={"layers/actors/x/artifacts/epic.artifact-template.md": "# t"})
        report = _checker(ws).catalog()
        assert not report.has_errors()

    def test_catalog_flags_colocated_schemas_and_legacy_names(self, tmp_path):
        ws = _tree(
            tmp_path,
            {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug"])},
            templates={
                "layers/actors/x/artifacts/stray.artifact.schema.json": "{}",
                "layers/actors/x/artifacts/old-template.md": "# legacy",
                "layers/actors/x/artifacts/old.artifact-contract.yaml": "legacy: true",
            },
        )
        messages = " | ".join(f.message for f in _checker(ws).catalog().findings)
        assert "must live in the schemas/ registry" in messages
        assert "legacy template filename" in messages
        assert "legacy artifact contract filename" in messages

    def test_check_json_missing_file(self, tmp_path):
        ws = _tree(tmp_path, {})
        report = _checker(ws).check_json(tmp_path / "ghost.artifact.json")
        assert any("not found" in f.message for f in report.findings)

    def test_check_json_invalid_json(self, tmp_path):
        ws = _tree(tmp_path, {})
        path = tmp_path / "bad.artifact.json"
        path.write_text("{broken")
        report = _checker(ws).check_json(path)
        assert any("invalid JSON" in f.message for f in report.findings)

    def test_check_json_requires_kind(self, tmp_path):
        ws = _tree(tmp_path, {})
        path = tmp_path / "no-kind.artifact.json"
        path.write_text(json.dumps({"slug": "x"}))
        report = _checker(ws).check_json(path)
        assert any("no string 'kind'" in f.message for f in report.findings)

    def test_check_json_validates_against_the_kind_schema(self, tmp_path):
        ws = _tree(tmp_path, {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug", "status"])})
        path = tmp_path / "unit.artifact.json"
        path.write_text(json.dumps({"kind": "epic", "slug": "x"}))
        report = _checker(ws).check_json(path)
        assert any("status" in f.message for f in report.findings)

    def test_check_json_passes_a_conforming_document(self, tmp_path):
        ws = _tree(tmp_path, {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug", "status"])})
        path = tmp_path / "unit.artifact.json"
        path.write_text(json.dumps({"kind": "epic", "slug": "x", "status": "done"}))
        assert not _checker(ws).check_json(path).has_errors()
