"""SchemaMapper — loads and validates the artifact-schema catalog + the harness schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    jsonschema = None

from models import Report
from text import bool_value, list_value
from .workspace import Workspace


class SchemaMapper:
    """The data-mapper for JSON schemas.

    `load_raw` returns artifact schemas as raw dicts (schema_id -> full schema dict with
    x-artifact metadata). Validating *artifacts against* these schemas is the `SchemaChecker`'s job.
    
    Alternative 1: schemas are pure data (raw dicts), not reified classes."""

    ARTIFACT_SCHEMA_SUFFIX = ".artifact.schema.json"
    ARTIFACT_TEMPLATE_SUFFIX = ".artifact-template.md"
    KNOWN_ARTIFACT_KINDS = {
        "adr",
        "architectural-vision",
        "architecture-decision-inventory",
        "architecture-review",
        "daily-sync",
        "epic",
        "feature",
        "feasibility-review",
        "gate-decision-backlog",
        "inspect-adapt",
        "kanban",
        "lean-business-case",
        "nfr-register",
        "operability-review",
        "pi-objectives",
        "pi-risks",
        "portfolio-init",
        "product-init",
        "product-vision",
        "project-brief",
        "qa-signoff",
        "roadmap",
        "runway-register",
        "security-review",
        "sprint-plan",
        "sprint-progress",
        "sprint-retro",
        "story",
        "strategic-themes",
        "testability-review",
        "ux-review",
        "value-review",
    }

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    # --- pure helpers -------------------------------------------------------
    @classmethod
    def template_stem_from_schema(cls, path: Path) -> str:
        name = path.name
        if name.endswith(cls.ARTIFACT_SCHEMA_SUFFIX):
            return name[: -len(cls.ARTIFACT_SCHEMA_SUFFIX)]
        return path.stem

    @classmethod
    def template_stem_from_template(cls, path: Path) -> str:
        name = path.name
        if name.endswith(cls.ARTIFACT_TEMPLATE_SUFFIX):
            return name[: -len(cls.ARTIFACT_TEMPLATE_SUFFIX)]
        return path.stem

    @staticmethod
    def schema_metadata(schema: dict[str, Any]) -> dict[str, Any]:
        metadata = schema.get("x-artifact")
        return metadata if isinstance(metadata, dict) else {}

    @staticmethod
    def format_schema_error(error: Any) -> str:
        data_path = ".".join(str(part) for part in error.absolute_path)
        prefix = f"{data_path}: " if data_path else ""
        return f"{prefix}{error.message}"

    # --- schema loading (load_raw) -----------------------------------------
    def load_raw(self, report: Report | None = None) -> dict[str, dict[str, Any]]:
        """Load artifact schemas as raw dicts (id -> full schema dict with x-artifact metadata).
        
        Returns: dict where key is schema_id and value is the full schema dict.
        This is the raw data model for Alternative 1 (schemas as pure data, not classes).
        """
        local_report = report or Report()
        root = self.workspace.skills_root
        registry = self.workspace.schemas_dir / "artifact"
        schemas_by_id: dict[str, dict[str, Any]] = {}
        seen_ids: dict[str, Path] = {}
        
        for path in sorted(registry.glob(f"*{self.ARTIFACT_SCHEMA_SUFFIX}")):
            label = self.workspace.label(path, root.parent)
            try:
                data = json.loads(self.workspace.read_text(path))
            except json.JSONDecodeError as exc:
                local_report.error(label, f"invalid JSON Schema file: {exc.msg} at line {exc.lineno} column {exc.colno}")
                continue
            if not isinstance(data, dict):
                local_report.error(label, "artifact schema must be a JSON object")
                continue
            
            if jsonschema is None:
                local_report.error(label, "jsonschema Python package is required for artifact schema validation")
                continue
            
            try:
                jsonschema.Draft7Validator.check_schema(data)
            except jsonschema.SchemaError as exc:
                path_suffix = "/".join(str(part) for part in exc.absolute_schema_path)
                location = f" at schema path {path_suffix}" if path_suffix else ""
                local_report.error(label, f"invalid Draft-07 JSON Schema{location}: {exc.message}")
                continue
            
            metadata = self.schema_metadata(data)
            schema_id = str(metadata.get("id") or "")
            if not schema_id:
                local_report.error(label, "x-artifact.id must not be empty")
                continue
            if schema_id != self.template_stem_from_schema(path):
                local_report.error(label, f"x-artifact.id {schema_id!r} must match filename stem {self.template_stem_from_schema(path)!r}")
                continue
            
            if schema_id in seen_ids:
                local_report.error(
                    label,
                    f"duplicate artifact schema id {schema_id!r}; first declared at {self.workspace.label(seen_ids[schema_id], root.parent)}",
                )
                continue
            
            seen_ids[schema_id] = path
            schemas_by_id[schema_id] = data
        
        return schemas_by_id

    # --- harness schemas ----------------------------------------------------
    def workflow_schema_path(self) -> Path:
        return self.workspace.schemas_dir / "workflow.schema.json"

    def workflow_schema(self) -> dict[str, Any] | None:
        path = self.workflow_schema_path()
        if not path.is_file():
            return None
        return json.loads(self.workspace.read_text(path))

    def journal_schema_path(self) -> Path:
        return self.workspace.schemas_dir / "journal.schema.json"

    def journal_schema(self) -> dict[str, Any] | None:
        path = self.journal_schema_path()
        if not path.is_file():
            return None
        return json.loads(self.workspace.read_text(path))

    def journal_payload_store(self) -> dict[str, Any]:
        """The per-command payload schemas keyed by `$id` — the store a $ref resolver uses to
        resolve the envelope's `oneOf` payload references (journal/<command>.payload.schema.json)."""
        store: dict[str, Any] = {}
        payload_dir = self.workspace.schemas_dir / "journal"
        if payload_dir.is_dir():
            for path in sorted(payload_dir.glob("*.payload.schema.json")):
                schema = json.loads(self.workspace.read_text(path))
                schema_id = schema.get("$id")
                if schema_id:
                    store[str(schema_id)] = schema
        return store
