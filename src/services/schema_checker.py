"""SchemaChecker — validates artifacts against their schemas + the catalog integrity."""

from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    jsonschema = None

from models import Artifact, ArtifactSchema, Report
from persistence import SchemaRepository, Workspace
from text import markdown_body, section_map, section_tree


class SchemaChecker:
    def __init__(self, workspace: Workspace, schemas: SchemaRepository) -> None:
        self.workspace = workspace
        self.schemas = schemas

    # --- helpers ------------------------------------------------------------
    def _matches_path(self, artifact: Artifact, artifact_schema: ArtifactSchema) -> bool:
        portfolio_root = self.workspace.portfolio_root
        try:
            relative = artifact.path.relative_to(portfolio_root).as_posix()
        except ValueError:
            relative = artifact.path.as_posix()
        if artifact.kind != artifact_schema.artifact_kind:
            return False
        return any(fnmatch(relative, pattern) for pattern in artifact_schema.path_patterns)

    def _schema_data(self, artifact: Artifact) -> dict:
        text = self.workspace.read_text(artifact.path)
        data = dict(artifact.fields)
        data["__path"] = self.workspace.label(artifact.path, self.workspace.portfolio_root)
        data["__kind"] = artifact.kind
        data["__artifact_id"] = artifact.artifact_id
        data["__frontmatter_present"] = bool(artifact.frontmatter)
        data["__sections"] = section_map(text)
        data["__section_tree"] = section_tree(text)
        data["__body"] = markdown_body(text)
        data["__product"] = artifact.product_slug
        return data

    # --- checks -------------------------------------------------------------
    def conformance(self, targets: list[Artifact]) -> Report:
        report = Report()
        schemas = self.schemas.load(report)
        if not schemas or not targets:
            return report
        for artifact in targets:
            path_matches = [schema for schema in schemas if self._matches_path(artifact, schema)]
            artifact_type = artifact.fields.get("type")
            if artifact_type in (None, ""):
                matches = path_matches
            else:
                matches = [
                    schema
                    for schema in path_matches
                    if schema.artifact_type is None or str(artifact_type) == schema.artifact_type
                ]
            label = self.workspace.label(artifact.path, self.workspace.portfolio_root)
            if not matches:
                report.error(label, "no artifact schema matches this artifact")
                continue
            if artifact_type in (None, "") and len(matches) > 1:
                variants = sorted(schema.schema_id for schema in matches)
                report.error(label, f"frontmatter field 'type' is required to select one artifact schema variant: {variants}")
                continue
            if len(matches) > 1:
                ids = ", ".join(schema.schema_id for schema in matches)
                report.error(label, f"multiple artifact schemas match this artifact: {ids}")
                continue
            artifact_schema = matches[0]
            validator = jsonschema.Draft7Validator(artifact_schema.schema)
            for error in sorted(validator.iter_errors(self._schema_data(artifact)), key=lambda item: list(item.absolute_path)):
                report.error(label, f"{artifact_schema.schema_id} schema violation: {self.schemas.format_schema_error(error)}")
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
