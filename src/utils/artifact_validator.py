"""ArtifactValidator — the mappers-level schema-conformance primitive.

Answers one question: does an `Artifact` conform to its matched artifact schema? It owns the
schema matching (path + `type` disambiguation), the `__`-injected validation view, and the
Draft-07 check. Shared by `SchemaChecker` (the reporter), `ArtifactMapper` (valid-by-
construction: `discover()` raises on any invalid artifact), and the postcondition hook (enforce +
revert). It logs nothing and knows nothing about commands — reporting stays a caller concern.

Dependencies are injected to avoid circular imports.

REFACTORED for Alternative 1: Works with raw schema dicts instead of ArtifactSchema objects.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    jsonschema = None

from models import Artifact, Report
from text import markdown_body, section_map, section_tree

if TYPE_CHECKING:
    from mappers import SchemaMapper, Workspace


class ArtifactValidator:
    """Schema-conformance for one artifact against the cataloged artifact schemas.
    
    Uses raw schema dicts (Alternative 1) instead of ArtifactSchema objects.
    Dependencies (Workspace, SchemaMapper) are injected to avoid circular imports.
    """

    def __init__(self, workspace: Workspace, schemas: SchemaMapper) -> None:
        self.workspace = workspace
        self.schemas = schemas
        self._catalog: dict[str, dict[str, Any]] | None = None

    def _schema_catalog(self) -> dict[str, dict[str, Any]]:
        """Load raw artifact schemas (id -> full schema dict)."""
        if self._catalog is None:
            self._catalog = self.schemas.load_raw(Report())
        return self._catalog

    # --- matching + validation view -----------------------------------------
    def _extract_metadata(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Extract x-artifact metadata from schema dict."""
        metadata = schema.get("x-artifact")
        return metadata if isinstance(metadata, dict) else {}

    def _matches_path(self, artifact: Artifact, schema: dict[str, Any]) -> bool:
        """Check if artifact path matches this schema's path patterns."""
        portfolio_root = self.workspace.portfolio_root
        try:
            relative = artifact.path.relative_to(portfolio_root).as_posix()
        except ValueError:
            relative = artifact.path.as_posix()
        
        metadata = self._extract_metadata(schema)
        artifact_kind = str(metadata.get("kind") or "")
        
        if artifact.kind != artifact_kind:
            return False
        
        path_patterns = metadata.get("pathPatterns", [])
        if not isinstance(path_patterns, list):
            path_patterns = [path_patterns]
        
        return any(fnmatch(relative, pattern) for pattern in path_patterns)

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

    def match(self, artifact: Artifact) -> tuple[dict[str, Any] | None, str | None, str | None]:
        """Resolve the single artifact schema for this artifact, or (None, reason, schema_id).
        
        Returns:
            (schema_dict, error_reason, schema_id) tuple.
            If match succeeds, schema_dict and schema_id are set, reason is None.
            If match fails, schema_dict is None, reason explains why.
        """
        schemas = self._schema_catalog()
        path_matches = [(sid, sch) for sid, sch in schemas.items() if self._matches_path(artifact, sch)]
        
        artifact_type = artifact.fields.get("type")
        if artifact_type in (None, ""):
            matches = path_matches
        else:
            filtered = []
            for sid, sch in path_matches:
                metadata = self._extract_metadata(sch)
                sch_type = metadata.get("type")
                if sch_type is None or str(artifact_type) == sch_type:
                    filtered.append((sid, sch))
            matches = filtered
        
        if not matches:
            return None, "no artifact schema matches this artifact", None
        if artifact_type in (None, "") and len(matches) > 1:
            variants = sorted(sid for sid, _ in matches)
            return None, f"frontmatter field 'type' is required to select one artifact schema variant: {variants}", None
        if len(matches) > 1:
            ids = ", ".join(sid for sid, _ in matches)
            return None, f"multiple artifact schemas match this artifact: {ids}", None
        
        schema_id, schema_dict = matches[0]
        return schema_dict, None, schema_id

    def validate(self, artifact: Artifact) -> Report:
        """Return a Report of schema violations for one artifact (empty Report ⇒ valid)."""
        report = Report()
        label = self.workspace.label(artifact.path, self.workspace.portfolio_root)
        if jsonschema is None:
            report.error(label, "jsonschema is required to validate artifacts")
            return report
        
        schema, reason, schema_id = self.match(artifact)
        if schema is None:
            report.error(label, reason or "no artifact schema matches this artifact")
            return report
        
        validator = jsonschema.Draft7Validator(schema)
        for error in sorted(validator.iter_errors(self._schema_data(artifact)), key=lambda item: list(item.absolute_path)):
            report.error(label, f"{schema_id} schema violation: {self.schemas.format_schema_error(error)}")
        
        # Validate body section structure if sections metadata is defined
        metadata = self._extract_metadata(schema)
        sections_spec_data = metadata.get("sections")
        if sections_spec_data:
            section_report = self._validate_sections(artifact, sections_spec_data)
            report.extend(section_report)
        
        return report

    def _validate_sections(self, artifact: Artifact, sections_spec_data: dict[str, Any]) -> Report:
        """Validate body section structure against sections spec metadata."""
        report = Report()
        label = self.workspace.label(artifact.path, self.workspace.portfolio_root)
        
        required = sections_spec_data.get("required", [])
        optional = sections_spec_data.get("optional", [])
        max_depth = sections_spec_data.get("maxDepth", 2)
        allow_unknown = sections_spec_data.get("allowUnknown", False)
        patterns = sections_spec_data.get("patterns", {})
        
        text = self.workspace.read_text(artifact.path)
        sections = section_map(text)
        section_tree_data = section_tree(text)

        # Check depth: all headings must be at or above max_depth
        def check_depth(tree: dict, depth: int) -> None:
            for heading, node in tree.items():
                if isinstance(node, dict):
                    level = node.get("__level", 2)
                    if level > max_depth + 1:  # +1 because h1 is depth 0
                        report.warn(
                            label,
                            f"section '{heading}' is at depth {level} (max allowed: {max_depth + 1})",
                        )
                    check_depth(node, depth + 1)

        check_depth(section_tree_data, 1)

        # Check required sections
        for required_section in required:
            if required_section not in sections:
                report.error(label, f"required section '{required_section}' is missing")

        # Check no unknown sections
        if not allow_unknown:
            all_allowed = set(required) | set(optional)
            unknown = set(sections.keys()) - all_allowed
            for unknown_section in unknown:
                report.warn(label, f"unknown section '{unknown_section}' (allowed: {', '.join(sorted(all_allowed))})")

        # Check content patterns if defined
        if patterns:
            for section_name, pattern in patterns.items():
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
