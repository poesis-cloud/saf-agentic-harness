"""ArtifactMapper — discovers workspace artifacts and resolves their relations.

The mapper is framework-agnostic: it does not embed methodology semantics. It learns the
recognized artifact kinds and their workspace paths from the framework schema catalog
(`schemas/*.artifact.schema.json`). Every artifact kind declares one or more
workspace-root-relative `pathPatterns`; the mapper matches on-disk files against those patterns
to infer kind and build the artifact universe.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from models import Artifact, Report
from text import frontmatter, parse_frontmatter, parse_sections, markdown_body, extract_file_heading
from utils import ArtifactValidator
from .workspace import Workspace


class InvalidArtifactError(Exception):
    """Raised by `discover()` when a persisted artifact violates its schema — a breach of the
    Workspace Validity Invariant (the workspace must contain exclusively schema-valid artifacts).
    Carries the offending path + the schema-violation `Report`."""

    def __init__(self, path: Path, report: Report) -> None:
        self.path = path
        self.report = report
        detail = "; ".join(f.message for f in report.findings) or "schema violation"
        super().__init__(f"invalid artifact {path}: {detail}")


class ArtifactMapper:
    """The data-mapper for the workspace artifacts.

    Two universe doors: `scan_raw()` returns every parsed artifact WITHOUT validation (for the
    validators — check-artifact + the postcondition hook — which must tolerate invalids to report
    them); `discover()` returns the VALID-BY-CONSTRUCTION domain universe and **raises
    `InvalidArtifactError`** on any schema-invalid artifact (Workspace Validity Invariant). Domain
    entry points (`resolve_unit`, `collect_by_schema_id`) go through `discover()`; navigation
    helpers read the raw universe (valid under the invariant, tolerant when it is being reconciled).
    """

    def __init__(
        self,
        workspace: Workspace,
        validator: ArtifactValidator | None = None,
        schemas: Any | None = None,
    ) -> None:
        self.workspace = workspace
        self.validator = validator
        self._schemas = schemas
        self._raw: list[Artifact] | None = None
        self._universe: list[Artifact] | None = None

    # --- schema-driven kind resolution --------------------------------------
    def _schema_mapper(self) -> Any:
        if self._schemas is None:
            # Lazy import avoids a hard dependency at construction time.
            from config import SchemaCatalog

            self._schemas = SchemaCatalog(self.workspace)
        return self._schemas

    def _schema_catalog(self) -> dict[str, dict[str, Any]]:
        return self._schema_mapper().load_raw(Report())

    @staticmethod
    def _pattern_matches(relative: str, pattern: str) -> bool:
        """Match a workspace-root-relative path against a schema pathPattern.

        Patterns use shell-style wildcards (`*`, `**` is treated as `*` for simplicity).
        A leading or embedded `**` is normalized to a single `*` because the framework
        catalog currently uses single-segment wildcards.
        """
        normalized = pattern.replace("**", "*")
        return fnmatch.fnmatch(relative, normalized) or fnmatch.fnmatch(relative, f"*/{normalized}")

    def _kinds_for_path(self, relative: str, text: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        """Return all (schema_id, schema_dict) pairs whose pathPatterns match `relative`.

        When multiple schemas match (e.g. business vs enabler variants share a directory
        convention), disambiguate by the artifact's `type` frontmatter if available. The caller
        decides whether to accept ambiguity.
        """
        matches: list[tuple[str, dict[str, Any]]] = []
        for schema_id, schema_dict in self._schema_catalog().items():
            metadata = schema_dict.get("x-artifact", {})
            patterns = metadata.get("pathPatterns") or []
            if not isinstance(patterns, list):
                patterns = [patterns]
            for pattern in patterns:
                if self._pattern_matches(relative, pattern):
                    matches.append((schema_id, schema_dict))
                    break
        if len(matches) <= 1 or text is None:
            return matches
        wanted = str(parse_frontmatter(frontmatter(text)).get("type") or "").strip()
        if wanted:
            typed = [(sid, sd) for sid, sd in matches if sd.get("x-artifact", {}).get("type") == wanted]
            if typed:
                return typed
        # Default to the business variant when type is silent.
        business = [(sid, sd) for sid, sd in matches if sd.get("x-artifact", {}).get("type") == "business"]
        if business:
            return business
        return matches

    def _kind_for_path(self, path: Path, text: str | None = None) -> str | None:
        """Infer the artifact kind for a single path, or None if no schema pattern matches."""
        workspace_root = self.workspace.workspace_root
        try:
            relative = path.resolve().relative_to(workspace_root.resolve()).as_posix()
        except (ValueError, OSError):
            return None
        matches = self._kinds_for_path(relative, text)
        return matches[0][0] if matches else None

    def _scope_slug_for_path(self, path: Path, kind: str) -> str | None:
        """Extract the scope slug for scoped artifacts from the path.

        The harness is agnostic to methodology scope names; it only preserves a scope slug
        when the matched schema pattern places the artifact under a recognized scope folder
        (currently `products/<slug>/`).
        """
        workspace_root = self.workspace.workspace_root
        try:
            parts = path.resolve().relative_to(workspace_root.resolve()).parts
        except (ValueError, OSError):
            return None
        if len(parts) >= 2 and parts[0] == "products":
            return parts[1]
        return None

    @staticmethod
    def _parse_file(kind: str, path: Path, text: str, scope_slug: str | None = None) -> Artifact:
        front = frontmatter(text)
        body = markdown_body(text)
        sections = parse_sections(body)
        heading = extract_file_heading(body)
        return Artifact(kind, path, parse_frontmatter(front), front, sections, scope_slug, heading)

    def scan_raw(self) -> list[Artifact]:
        """Every parsed workspace artifact, WITHOUT schema validation (cached). Used by the
        validators that must tolerate invalids to report them; also the base of `discover()`."""
        if self._raw is None:
            self._raw = self._scan()
        return self._raw

    def discover(self) -> list[Artifact]:
        """The valid-by-construction domain universe (cached). Raises `InvalidArtifactError` on the
        first schema-invalid artifact — under the Workspace Validity Invariant this never happens in
        normal operation; if it does, it is a breach and must fail fast."""
        if self._universe is None:
            raw = self.scan_raw()
            if self.validator is not None:
                for artifact in raw:
                    report = self.validator.validate(artifact)
                    if report.has_errors():
                        raise InvalidArtifactError(artifact.path, report)
            self._universe = raw
        return self._universe

    def load_one(self, path: Path) -> Artifact | None:
        """Parse a single workspace file into an (unvalidated) `Artifact`, inferring its kind from
        the path against the schema catalog, or None if the path is not a workspace artifact
        location. Used by the postcondition hook to validate exactly the just-written file."""
        if not path.is_file():
            return None
        workspace_root = self.workspace.workspace_root
        try:
            path.relative_to(workspace_root.resolve())
        except (ValueError, OSError):
            return None
        text = self.workspace.read_text(path)
        kind = self._kind_for_path(path, text)
        if kind is None:
            return None
        scope_slug = self._scope_slug_for_path(path, kind)
        return self._parse_file(kind, path, text, scope_slug)

    def _scan(self) -> list[Artifact]:
        workspace_root = self.workspace.workspace_root
        artifacts: list[Artifact] = []
        if not workspace_root.is_dir():
            return artifacts

        # Collect every file that any schema pattern could match. We scan markdown and JSON
        # artifact files; other extensions are ignored.
        for path in sorted(workspace_root.rglob("*")):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            if path.suffix not in (".md", ".json"):
                continue
            # Skip logs and hidden directories.
            try:
                relative = path.resolve().relative_to(workspace_root.resolve()).as_posix()
            except (ValueError, OSError):
                continue
            if relative.startswith("logs/"):
                continue

            text = self.workspace.read_text(path)
            kind = self._kind_for_path(path, text)
            if kind is None:
                continue
            scope_slug = self._scope_slug_for_path(path, kind)
            artifacts.append(self._parse_file(kind, path, text, scope_slug))

        return artifacts

    # --- selection ----------------------------------------------------------
    def select(self, unit_id: str | None, kinds: set[str] | None) -> list[Artifact]:
        targets = self.scan_raw()
        if kinds is not None:
            targets = [artifact for artifact in targets if artifact.kind in kinds]
        if unit_id is not None:
            targets = [artifact for artifact in targets if artifact.artifact_id == unit_id]
        return targets

    def resolve_unit(self, unit_id: str) -> Artifact | None:
        matches = [artifact for artifact in self.discover() if artifact.artifact_id == unit_id]
        return matches[0] if matches else None

    def collect_by_schema_id(self, schema_id: str) -> list[Artifact]:
        """Collect all artifacts matching a schema_id (maps to artifact.kind).
        Used by CEL set-selector to enumerate artifacts for state conditions."""
        return [artifact for artifact in self.discover() if artifact.kind == schema_id]

    def ambiguity_error(self, report: Report, targets: list[Artifact], unit_id: str | None) -> bool:
        if unit_id and len(targets) > 1:
            locations = sorted(self.workspace.label(target.path, self.workspace.workspace_root) for target in targets)
            report.error(self.workspace.workspace_root, f"unit id {unit_id!r} is not globally unique (found in {locations}); ids must be unique across the workspace — rename to a unique slug")
            return False
        return True

    # --- relations ----------------------------------------------------------
    def children_of(self, artifact: Artifact, child_kind: str, parent_field: str) -> list[Artifact]:
        """Generic parent→children traversal using the `parent_<kind>` convention.

        Relations are resolved globally by artifact id; the harness does not enforce a scope
        boundary because parent/child links may cross framework scopes.
        """
        return [
            candidate
            for candidate in self.scan_raw()
            if candidate.kind == child_kind
            and str(candidate.fields.get(parent_field)) == artifact.artifact_id
        ]

    def parent_of(self, artifact: Artifact, parent_kind: str, parent_field: str) -> Artifact | None:
        """Generic child→parent traversal using the `parent_<kind>` convention."""
        parent_id = str(artifact.fields.get(parent_field))
        return next(
            (a for a in self.scan_raw() if a.kind == parent_kind and a.artifact_id == parent_id),
            None,
        )

    def resolve_dependency(self, dependency_id: str) -> list[Artifact]:
        return [artifact for artifact in self.scan_raw() if artifact.artifact_id == dependency_id]

    # --- locators -----------------------------------------------------------
    def scope_root(self, artifact: Artifact) -> Path:
        """Return the workspace root for unscoped artifacts, or the scope folder for scoped
        artifacts. The harness does not know methodology scope names; it only preserves the
        scope slug when one is encoded in the artifact path."""
        if artifact.scope_slug is None:
            return self.workspace.workspace_root
        return self.workspace.workspace_root / "products" / artifact.scope_slug

    def find_artifact(self, scope_dir: Path, subdir: str, artifact_id: str, suffix: str = "*.md") -> Path | None:
        """Generic artifact locator: find a file under <scope_dir>/<subdir>/ whose stem contains
        the given artifact id. Framework-specific locators (ADR, QA signoff) are thin wrappers."""
        normalized = artifact_id.lower()
        target_dir = scope_dir / subdir
        if not target_dir.is_dir():
            return None
        for path in target_dir.glob(suffix):
            if normalized in path.stem.lower():
                return path
        return None

    def find_qa_signoff(self, artifact: Artifact) -> list[Path]:
        """Locate QA signoff files adjacent to the artifact. The exact folder naming is a framework
        convention; the harness only provides this helper for backward compatibility."""
        parent_dir = artifact.path.parent
        if not parent_dir.is_dir():
            return []
        return sorted(parent_dir.glob(f"{artifact.artifact_id}*signoff*.md"))
