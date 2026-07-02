"""SchemaChecker — reporter over the schema-conformance primitive + catalog + native-JSON checks."""

from __future__ import annotations

import json
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    jsonschema = None

from models import Artifact, Report
from mappers import SchemaRepository, Workspace
from utils import ArtifactValidator


class SchemaChecker:
    """The reporting surface for artifact schema conformance. The per-artifact check is the
    mappers-level `ArtifactValidator` (shared with `ArtifactRepository` + the postcondition
    hook); this service loops it into a report and adds catalog integrity + native-JSON validation."""

    def __init__(self, workspace: Workspace, schemas: SchemaRepository, validator: ArtifactValidator | None = None) -> None:
        self.workspace = workspace
        self.schemas = schemas
        self.validator = validator or ArtifactValidator(workspace, schemas)

    # --- checks -------------------------------------------------------------
    def conformance(self, targets: list[Artifact]) -> Report:
        report = Report()
        for artifact in targets:
            report.extend(self.validator.validate(artifact))
        return report

    def catalog(self) -> Report:
        report = Report()
        root = self.workspace.skills_root
        schemas = self.schemas.load(report)
        template_suffix = self.schemas.ARTIFACT_TEMPLATE_SUFFIX
        schema_suffix = self.schemas.ARTIFACT_SCHEMA_SUFFIX
        # Every artifact template colocated with a role/orchestration (`**/artifacts/`) must be
        # claimed by exactly one registry schema whose x-artifact.template points at it. The
        # registry (harness/schemas/artifact) is the single home for schemas; templates stay next
        # to the workflow that renders them.
        claimed = {schema.template_path.resolve() for schema in schemas if schema.template_path}
        for template_path in sorted(root.glob(f"**/artifacts/*{template_suffix}")):
            if template_path.resolve() not in claimed:
                report.error(self.workspace.label(template_path, root.parent), "no registry artifact schema declares this template via x-artifact.template")

        # A schema must live in the harness/schemas/artifact registry, never colocated under a
        # role/orchestration `artifacts/` dir.
        for stray_schema in sorted(root.glob(f"**/artifacts/*{schema_suffix}")):
            report.error(self.workspace.label(stray_schema, root.parent), "artifact schema must live in the harness/schemas/artifact registry, not colocated under an artifacts/ dir")

        legacy_templates = sorted(
            path for path in root.glob("**/artifacts/*-template.md") if not path.name.endswith(template_suffix)
        )
        for legacy_path in legacy_templates:
            report.error(self.workspace.label(legacy_path, root.parent), f"legacy template filename must be renamed to *{template_suffix}")

        legacy_contracts = sorted(root.glob("**/artifacts/*.artifact-contract.yaml"))
        for legacy_path in legacy_contracts:
            report.error(self.workspace.label(legacy_path, root.parent), f"legacy artifact contract filename must be replaced by *{schema_suffix}")

        if not schemas:
            report.warn(root, "no artifact schemas found")
        return report

    def check_json(self, json_path: Path) -> Report:
        """Validate a native JSON artifact (`*.artifact.json`) directly against its schema."""
        report = Report()
        if not json_path.is_file():
            report.error(json_path, "artifact JSON file not found")
            return report
        try:
            data = json.loads(self.workspace.read_text(json_path))
        except json.JSONDecodeError as exc:
            report.error(json_path, f"invalid JSON: {exc}")
            return report
        if jsonschema is None:
            report.error(json_path, "jsonschema is required to validate artifacts")
            return report
        label = self.workspace.label(json_path, self.workspace.framework_root)
        kind = data.get("kind")
        if not isinstance(kind, str) or not kind:
            report.error(label, "artifact JSON has no string 'kind' field to select a schema")
            return report
        schemas = self.schemas.load(report)
        matches = [schema for schema in schemas if schema.artifact_kind == kind]
        if not matches:
            report.error(label, f"no artifact schema declares x-artifact.kind {kind!r}")
            return report
        if len(matches) > 1:
            report.error(label, f"multiple artifact schemas declare kind {kind!r}: {', '.join(schema.schema_id for schema in matches)}")
            return report
        validator = jsonschema.Draft7Validator(matches[0].schema)
        for err in sorted(validator.iter_errors(data), key=lambda item: list(item.absolute_path)):
            report.error(label, f"{matches[0].schema_id} schema violation: {self.schemas.format_schema_error(err)}")
        return report
