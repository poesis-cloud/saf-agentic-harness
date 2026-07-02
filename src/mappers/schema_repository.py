"""SchemaRepository — loads and validates the artifact-schema catalog + the harness schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    jsonschema = None

from models import ArtifactSchema, Report
from text import bool_value, list_value
from .workspace import Workspace


class SchemaRepository:
    """The data-mapper for JSON schemas.

    `load` deserializes every `*.artifact.schema.json` into an `ArtifactSchema` (validating
    the schema file itself as it maps it — an invalid source can't yield a model); the
    `workflow_schema` / `journal_schema` accessors load the two harness schemas. Validating
    *artifacts against* these schemas is the `SchemaChecker`'s job.
    """

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

    # --- schema-file mapping + validation -----------------------------------
    def validate_schema_file(self, path: Path, skills_dir: Path, report: Report) -> ArtifactSchema | None:
        label = self.workspace.label(path, skills_dir.parent)
        try:
            data = json.loads(self.workspace.read_text(path))
        except json.JSONDecodeError as exc:
            report.error(label, f"invalid JSON Schema file: {exc.msg} at line {exc.lineno} column {exc.colno}")
            return None
        if not isinstance(data, dict):
            report.error(label, "artifact schema must be a JSON object")
            return None
        if jsonschema is None:
            report.error(label, "jsonschema Python package is required for artifact schema validation")
            return None
        try:
            jsonschema.Draft7Validator.check_schema(data)
        except jsonschema.SchemaError as exc:
            path_suffix = "/".join(str(part) for part in exc.absolute_schema_path)
            location = f" at schema path {path_suffix}" if path_suffix else ""
            report.error(label, f"invalid Draft-07 JSON Schema{location}: {exc.message}")
            return None

        metadata = self.schema_metadata(data)
        schema_id = str(metadata.get("id") or "")
        if not schema_id:
            report.error(label, "x-artifact.id must not be empty")
        if schema_id and schema_id != self.template_stem_from_schema(path):
            report.error(label, f"x-artifact.id {schema_id!r} must match filename stem {self.template_stem_from_schema(path)!r}")

        artifact_kind = str(metadata.get("kind") or "")
        if artifact_kind not in self.KNOWN_ARTIFACT_KINDS:
            report.error(label, f"unknown x-artifact.kind {artifact_kind!r}")

        artifact_type = metadata.get("type")
        if artifact_type is not None and str(artifact_type) not in {"business", "enabler", "user"}:
            report.error(label, f"unsupported x-artifact.type {artifact_type!r}")

        template_value = str(metadata.get("template") or "")
        template_path = skills_dir / template_value
        if not template_value.endswith(self.ARTIFACT_TEMPLATE_SUFFIX):
            report.error(label, f"template {template_value!r} must end with {self.ARTIFACT_TEMPLATE_SUFFIX}")
        if template_path.name and not template_path.is_file():
            report.error(label, f"template file does not exist: {template_value}")
        if template_path.is_file() and self.template_stem_from_template(template_path) != schema_id:
            report.error(label, f"template {template_path.name!r} must share artifact_id stem {schema_id!r}")

        path_patterns = list_value(metadata.get("pathPatterns"))
        if not path_patterns:
            report.error(label, "x-artifact.pathPatterns must not be empty")
        for pattern in path_patterns:
            if pattern.startswith("/") or ".." in Path(pattern).parts:
                report.error(label, f"path pattern {pattern!r} must be portfolio-relative and must not contain '..'")

        if report.findings and any(finding.path == label and finding.severity == "error" for finding in report.findings):
            return None

        # Load section spec if defined
        sections_data = metadata.get("sections")
        sections_spec = None
        if sections_data:
            from models import SectionSpec
            sections_spec = SectionSpec.from_dict(sections_data)

        return ArtifactSchema(
            path=path,
            schema_id=schema_id,
            schema=data,
            artifact_kind=artifact_kind,
            artifact_type=str(artifact_type) if artifact_type is not None else None,
            template_path=template_path,
            path_patterns=path_patterns,
            render_only=bool_value(metadata.get("renderOnly", False)),
            sections_spec=sections_spec,
        )

    def load(self, report: Report | None = None) -> list[ArtifactSchema]:
        local_report = report or Report()
        root = self.workspace.skills_root
        registry = self.workspace.schemas_dir / "artifact"
        schemas: list[ArtifactSchema] = []
        seen_ids: dict[str, Path] = {}
        for path in sorted(registry.glob(f"*{self.ARTIFACT_SCHEMA_SUFFIX}")):
            artifact_schema = self.validate_schema_file(path, root, local_report)
            if artifact_schema is None:
                continue
            if artifact_schema.schema_id in seen_ids:
                local_report.error(
                    self.workspace.label(path, root.parent),
                    f"duplicate artifact schema id {artifact_schema.schema_id!r}; first declared at {self.workspace.label(seen_ids[artifact_schema.schema_id], root.parent)}",
                )
                continue
            seen_ids[artifact_schema.schema_id] = path
            schemas.append(artifact_schema)
        return schemas

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
