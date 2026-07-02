"""ArtifactMapper — discovers portfolio artifacts and resolves their relations."""

from __future__ import annotations

from pathlib import Path

from models import Artifact, Report
from text import frontmatter, parse_frontmatter, parse_sections, markdown_body
from utils import ArtifactValidator
from .workspace import Workspace


class InvalidArtifactError(Exception):
    """Raised by `discover()` when a persisted artifact violates its schema — a breach of the
    Portfolio Validity Invariant (the portfolio must contain exclusively schema-valid artifacts).
    Carries the offending path + the schema-violation `Report`."""

    def __init__(self, path: Path, report: Report) -> None:
        self.path = path
        self.report = report
        detail = "; ".join(f.message for f in report.findings) or "schema violation"
        super().__init__(f"invalid artifact {path}: {detail}")


class ArtifactMapper:
    """The data-mapper for the portfolio (Epic/Feature/Story).

    Two universe doors: `scan_raw()` returns every parsed artifact WITHOUT validation (for the
    validators — check-artifact + the postcondition hook — which must tolerate invalids to report
    them); `discover()` returns the VALID-BY-CONSTRUCTION domain universe and **raises
    `InvalidArtifactError`** on any schema-invalid artifact (Portfolio Validity Invariant). Domain
    entry points (`resolve_unit`, `collect_by_schema_id`) go through `discover()`; navigation
    helpers read the raw universe (valid under the invariant, tolerant when it is being reconciled).
    """

    def __init__(self, workspace: Workspace, validator: ArtifactValidator | None = None) -> None:
        self.workspace = workspace
        self.validator = validator
        self._raw: list[Artifact] | None = None
        self._universe: list[Artifact] | None = None

    # --- discovery ----------------------------------------------------------
    @staticmethod
    def _parse_file(kind: str, path: Path, text: str, product_slug: str | None = None) -> Artifact:
        front = frontmatter(text)
        body = markdown_body(text)
        sections = parse_sections(body)
        return Artifact(kind, path, parse_frontmatter(front), front, sections, product_slug)

    def scan_raw(self) -> list[Artifact]:
        """Every parsed portfolio artifact, WITHOUT schema validation (cached). Used by the
        validators that must tolerate invalids to report them; also the base of `discover()`."""
        if self._raw is None:
            self._raw = self._scan()
        return self._raw

    def discover(self) -> list[Artifact]:
        """The valid-by-construction domain universe (cached). Raises `InvalidArtifactError` on the
        first schema-invalid artifact — under the Portfolio Validity Invariant this never happens in
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
        """Parse a single portfolio file into an (unvalidated) `Artifact`, inferring its kind from
        the path, or None if the path is not a portfolio artifact location. Used by the postcondition
        hook to validate exactly the just-written file."""
        portfolio_root = self.workspace.portfolio_root
        try:
            parts = path.resolve().relative_to(portfolio_root.resolve()).parts
        except (ValueError, OSError):
            return None
        if not path.is_file() or path.suffix != ".md":
            return None
        text = self.workspace.read_text(path)
        if len(parts) == 2 and parts[0] == "epics":
            return self._parse_file("epic", path, text)
        if len(parts) == 3 and parts[1] == "features":
            return self._parse_file("feature", path, text, parts[0])
        if len(parts) >= 4 and parts[1].startswith("sprint-") and parts[2] == "stories":
            return self._parse_file("story", path, text, parts[0])
        return None

    def _scan(self) -> list[Artifact]:
        portfolio_root = self.workspace.portfolio_root
        artifacts: list[Artifact] = []
        for path in sorted(portfolio_root.glob("epics/*.md")):
            if path.name.startswith("."):
                continue
            artifacts.append(self._parse_file("epic", path, self.workspace.read_text(path)))
        for path in sorted(portfolio_root.glob("*/features/*.md")):
            if path.name.startswith("."):
                continue
            product_slug = path.relative_to(portfolio_root).parts[0]
            artifacts.append(self._parse_file("feature", path, self.workspace.read_text(path), product_slug))
        for path in sorted(portfolio_root.glob("*/sprint-*/stories/*.md")):
            if path.name.startswith("."):
                continue
            product_slug = path.relative_to(portfolio_root).parts[0]
            artifacts.append(self._parse_file("story", path, self.workspace.read_text(path), product_slug))
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
        Used by CEL set-selector to enumerate artifacts for state conditions.
        Returns empty list if schema_id doesn't match any known kind (epic, feature, story)."""
        return [artifact for artifact in self.discover() if artifact.kind == schema_id]

    def ambiguity_error(self, report: Report, targets: list[Artifact], unit_id: str | None) -> bool:
        if unit_id and len(targets) > 1:
            locations = sorted(str(target.product_slug or "portfolio") for target in targets)
            report.error(self.workspace.portfolio_root, f"unit id {unit_id!r} is not globally unique (found in {locations}); ids must be unique across the portfolio — rename to a unique slug")
            return False
        return True

    # --- relations ----------------------------------------------------------
    def child_stories(self, feature: Artifact) -> list[Artifact]:
        return [
            candidate
            for candidate in self.scan_raw()
            if candidate.kind == "story"
            and candidate.product_slug == feature.product_slug
            and str(candidate.fields.get("parent_feature")) == feature.artifact_id
        ]

    def child_features(self, epic: Artifact) -> list[Artifact]:
        return [
            candidate
            for candidate in self.scan_raw()
            if candidate.kind == "feature" and str(candidate.fields.get("parent_epic")) == epic.artifact_id
        ]

    def parent_feature_of(self, story: Artifact) -> Artifact | None:
        parent_id = str(story.fields.get("parent_feature"))
        return next(
            (a for a in self.scan_raw() if a.kind == "feature" and a.product_slug == story.product_slug and a.artifact_id == parent_id),
            None,
        )

    def parent_epic_of(self, feature: Artifact) -> Artifact | None:
        parent_id = str(feature.fields.get("parent_epic"))
        return next((a for a in self.scan_raw() if a.kind == "epic" and a.artifact_id == parent_id), None)

    def priced_children(self, artifact: Artifact) -> list[Artifact]:
        if artifact.kind == "feature":
            return self.child_stories(artifact)
        if artifact.kind == "epic":
            return self.child_features(artifact)
        return []

    def resolve_dependency(self, dependency_id: str) -> list[Artifact]:
        return [artifact for artifact in self.scan_raw() if artifact.artifact_id == dependency_id]

    # --- locators -----------------------------------------------------------
    def product_root(self, artifact: Artifact) -> Path:
        if artifact.product_slug is None:
            return self.workspace.portfolio_root
        return self.workspace.portfolio_root / artifact.product_slug

    def find_adr(self, product_dir: Path, adr_id: str) -> Path | None:
        normalized = adr_id.lower()
        architecture_dir = product_dir / "architecture"
        if not architecture_dir.is_dir():
            return None
        for path in architecture_dir.glob("*.md"):
            if normalized in path.stem.lower():
                return path
        return None

    def find_qa_signoff(self, story: Artifact) -> list[Path]:
        sprint_dir = story.path.parent.parent
        qa_dir = sprint_dir / "qa"
        if not qa_dir.is_dir():
            return []
        return sorted(qa_dir.glob(f"{story.artifact_id}*signoff*.md"))
