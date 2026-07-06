"""Unit tests — ArtifactValidator: schema matching (path patterns + `type` disambiguation)
and validation against the $ref-subtyped catalog. Uses a synthetic colocated framework tree so
matching rules are exercised in isolation from the real catalog (which is covered in
tests/integration/)."""

from __future__ import annotations

import json
from pathlib import Path

from config import SchemaCatalog
from mappers import Workspace
from models import Artifact
from utils import ArtifactValidator

BASE_CONTRACT = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://poesis.cloud/safe-agentic-framework/artifact.schema.json",
    "type": "object",
    "required": ["slug"],
    "properties": {"slug": {"type": "string", "pattern": "^[a-z0-9-]+$"}},
}


def _schema(kind: str, patterns: list[str], required: list[str], variant: str | None = None) -> dict:
    meta = {"id": kind if variant is None else f"{kind}-{variant}", "kind": kind, "pathPatterns": patterns}
    if variant is not None:
        meta["type"] = variant
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://poesis.cloud/safe-agentic-framework/{meta['id']}.artifact.schema.json",
        "$ref": "https://poesis.cloud/safe-agentic-framework/artifact.schema.json",
        "type": "object",
        "x-artifact": meta,
        "required": required,
        "properties": {field: {"type": "string"} for field in required},
    }


def _tree(tmp_path: Path, schemas: dict[str, dict]) -> Workspace:
    (tmp_path / "harness" / "contracts").mkdir(parents=True)
    (tmp_path / "harness" / "contracts" / "artifact.schema.json").write_text(json.dumps(BASE_CONTRACT))
    (tmp_path / "schemas").mkdir()
    for schema_id, doc in schemas.items():
        (tmp_path / "schemas" / f"{schema_id}.artifact.schema.json").write_text(json.dumps(doc))
    (tmp_path / "workspace").mkdir()
    return Workspace(tmp_path, tmp_path / "workspace")


def _validator(ws: Workspace) -> ArtifactValidator:
    return ArtifactValidator(ws, SchemaCatalog(ws))


def _artifact(ws: Workspace, rel: str, kind: str, fields: dict) -> Artifact:
    path = ws.workspace_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\nstub: true\n---\n")
    return Artifact(kind, path, fields, "")


class TestArtifactValidator:
    def test_match_resolves_by_kind_and_path_pattern(self, tmp_path):
        ws = _tree(tmp_path, {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug", "status"])})
        schema, reason, schema_id = _validator(ws).match(_artifact(ws, "backlog/x.epic.md", "epic", {}))
        assert reason is None
        assert schema_id == "epic"

    def test_no_match_reports_a_reason(self, tmp_path):
        ws = _tree(tmp_path, {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug"])})
        schema, reason, _ = _validator(ws).match(_artifact(ws, "elsewhere/x.md", "epic", {}))
        assert schema is None
        assert "no artifact schema matches" in reason

    def test_type_field_disambiguates_variants(self, tmp_path):
        ws = _tree(tmp_path, {
            "review-a": _schema("review", ["reviews/*.md"], ["slug"], variant="a"),
            "review-b": _schema("review", ["reviews/*.md"], ["slug"], variant="b"),
        })
        art = _artifact(ws, "reviews/x.md", "review", {"type": "b"})
        _, reason, schema_id = _validator(ws).match(art)
        assert reason is None
        assert schema_id == "review-b"

    def test_ambiguous_match_without_type_is_an_error(self, tmp_path):
        ws = _tree(tmp_path, {
            "review-a": _schema("review", ["reviews/*.md"], ["slug"], variant="a"),
            "review-b": _schema("review", ["reviews/*.md"], ["slug"], variant="b"),
        })
        _, reason, _ = _validator(ws).match(_artifact(ws, "reviews/x.md", "review", {}))
        assert "'type' is required" in reason

    def test_validate_reports_local_and_inherited_violations(self, tmp_path):
        ws = _tree(tmp_path, {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug", "status"])})
        art = _artifact(ws, "backlog/x.epic.md", "epic", {"slug": "NOT VALID SLUG"})
        report = _validator(ws).validate(art)
        messages = " | ".join(f.message for f in report.findings)
        assert "status" in messages          # local required property
        assert "NOT VALID SLUG" in messages  # inherited base-contract slug pattern

    def test_validate_passes_a_conforming_artifact(self, tmp_path):
        ws = _tree(tmp_path, {"epic": _schema("epic", ["backlog/*.epic.md"], ["slug", "status"])})
        art = _artifact(ws, "backlog/x.epic.md", "epic", {"slug": "x", "status": "done"})
        assert not _validator(ws).validate(art).has_errors()
