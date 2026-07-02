"""ArtifactValidator — the mappers-level schema-conformance primitive.

Answers one question: does an `Artifact` conform to its matched artifact schema? It owns the
schema matching (path + `type` disambiguation), the `__`-injected validation view, and the
Draft-07 check. Shared by `SchemaChecker` (the reporter), `ArtifactRepository` (valid-by-
construction: `discover()` raises on any invalid artifact), and the postcondition hook (enforce +
revert). It logs nothing and knows nothing about commands — reporting stays a caller concern.

Dependencies are injected to avoid circular imports.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    jsonschema = None

from models import Artifact, ArtifactSchema, Report
from text import markdown_body, section_map, section_tree

if TYPE_CHECKING:
    from mappers import SchemaRepository, Workspace


class ArtifactValidator:
    """Schema-conformance for one artifact against the cataloged artifact schemas.
    
    Dependencies (Workspace, SchemaRepository) are injected to avoid circular imports.
    """

    def __init__(self, workspace: Workspace, schemas: SchemaRepository) -> None:
        self.workspace = workspace
        self.schemas = schemas
        self._catalog: list[ArtifactSchema] | None = None

    def _schema_catalog(self) -> list[ArtifactSchema]:
        if self._catalog is None:
            self._catalog = self.schemas.load(Report())
        return self._catalog

    # --- matching + validation view -----------------------------------------
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

    def match(self, artifact: Artifact) -> tuple[ArtifactSchema | None, str | None]:
        """Resolve the single artifact schema for this artifact, or (None, reason)."""
        schemas = self._schema_catalog()
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
        if not matches:
            return None, "no artifact schema matches this artifact"
        if artifact_type in (None, "") and len(matches) > 1:
            variants = sorted(schema.schema_id for schema in matches)
            return None, f"frontmatter field 'type' is required to select one artifact schema variant: {variants}"
        if len(matches) > 1:
            ids = ", ".join(schema.schema_id for schema in matches)
            return None, f"multiple artifact schemas match this artifact: {ids}"
        return matches[0], None

    def validate(self, artifact: Artifact) -> Report:
        """Return a Report of schema violations for one artifact (empty Report ⇒ valid)."""
        report = Report()
        label = self.workspace.label(artifact.path, self.workspace.portfolio_root)
        if jsonschema is None:
            report.error(label, "jsonschema is required to validate artifacts")
            return report
        artifact_schema, reason = self.match(artifact)
        if artifact_schema is None:
            report.error(label, reason or "no artifact schema matches this artifact")
            return report
        validator = jsonschema.Draft7Validator(artifact_schema.schema)
        for error in sorted(validator.iter_errors(self._schema_data(artifact)), key=lambda item: list(item.absolute_path)):
            report.error(label, f"{artifact_schema.schema_id} schema violation: {self.schemas.format_schema_error(error)}")
        
        # Validate body section structure if sections_spec is defined
        if artifact_schema.sections_spec:
            section_report = self._validate_sections(artifact, artifact_schema)
            report.extend(section_report)
        
        return report

    def _validate_sections(self, artifact: Artifact, artifact_schema: ArtifactSchema) -> Report:
        """Validate body section structure against the schema's sections_spec."""
        from models import SectionSpec

        report = Report()
        label = self.workspace.label(artifact.path, self.workspace.portfolio_root)
        spec = artifact_schema.sections_spec
        if not spec:
            return report

        text = self.workspace.read_text(artifact.path)
        sections = section_map(text)
        section_tree_data = section_tree(text)

        # Check depth: all headings must be at or above max_depth
        def check_depth(tree: dict, depth: int) -> None:
            for heading, node in tree.items():
                if isinstance(node, dict):
                    level = node.get("__level", 2)
                    if level > spec.max_depth + 1:  # +1 because h1 is depth 0
                        report.warn(
                            label,
                            f"section '{heading}' is at depth {level} (max allowed: {spec.max_depth + 1})",
                        )
                    check_depth(node, depth + 1)

        check_depth(section_tree_data, 1)

        # Check required sections
        for required_section in spec.required:
            if required_section not in sections:
                report.error(label, f"required section '{required_section}' is missing")

        # Check no unknown sections
        if not spec.allow_unknown:
            unknown = set(sections.keys()) - spec.all_sections
            for unknown_section in unknown:
                report.warn(label, f"unknown section '{unknown_section}' (allowed: {', '.join(sorted(spec.all_sections))})")

        # Check content patterns if defined
        if spec.patterns:
            for section_name, pattern in spec.patterns.items():
                if section_name in sections:
                    self._validate_section_pattern(label, section_name, sections[section_name], pattern, report)

        return report

    def _validate_section_pattern(self, label: str, section_name: str, content: str, pattern: str, report: Report) -> None:
        """Validate content against expected pattern (e.g., bullet_list, code_block, table)."""
        lines = content.strip().split("\n")
        
        if pattern == "bullet_list":
            # Expected format: lines starting with "- " or "* "
            bullet_lines = [line for line in lines if line.strip() and (line.strip().startswith("- ") or line.strip().startswith("* "))]
            if not bullet_lines:
                report.warn(label, f"section '{section_name}' should contain bullet points (pattern: {pattern})")
        elif pattern == "table":
            # Expected format: lines with pipe separators |
            table_lines = [line for line in lines if "|" in line]
            if not table_lines:
                report.warn(label, f"section '{section_name}' should contain a table (pattern: {pattern})")
        elif pattern == "code_block":
            # Expected format: code blocks with ``` markers
            if "```" not in content:
                report.warn(label, f"section '{section_name}' should contain code blocks (pattern: {pattern})")
        # Add more patterns as needed

    def is_valid(self, artifact: Artifact) -> bool:
        return not self.validate(artifact).has_errors()
