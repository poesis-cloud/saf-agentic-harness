"""ArtifactRepository — discovers portfolio artifacts and resolves their relations."""

from __future__ import annotations

from pathlib import Path

from models import Artifact, Report
from text import frontmatter, parse_frontmatter
from .workspace import Workspace


class ArtifactRepository:
    """The data-mapper for the portfolio (Epic/Feature/Story).

    `discover` scans the portfolio once (cached per instance) into `Artifact` entities;
    the selection, relation, and locator methods all read that universe so callers never
    pass it around. This is the domain layer's single source of artifact truth.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self._universe: list[Artifact] | None = None

    # --- discovery ----------------------------------------------------------
    def discover(self) -> list[Artifact]:
        if self._universe is None:
            self._universe = self._scan()
        return self._universe

    def _scan(self) -> list[Artifact]:
        portfolio_root = self.workspace.portfolio_root
        artifacts: list[Artifact] = []
        for path in sorted(portfolio_root.glob("epics/*.md")):
            if path.name.startswith("."):
                continue
            text = self.workspace.read_text(path)
            front = frontmatter(text)
            artifacts.append(Artifact("epic", path, parse_frontmatter(front), front))
        for path in sorted(portfolio_root.glob("*/features/*.md")):
            if path.name.startswith("."):
                continue
            text = self.workspace.read_text(path)
            front = frontmatter(text)
            product_slug = path.relative_to(portfolio_root).parts[0]
            artifacts.append(Artifact("feature", path, parse_frontmatter(front), front, product_slug))
        for path in sorted(portfolio_root.glob("*/sprint-*/stories/*.md")):
            if path.name.startswith("."):
                continue
            text = self.workspace.read_text(path)
            front = frontmatter(text)
            product_slug = path.relative_to(portfolio_root).parts[0]
            artifacts.append(Artifact("story", path, parse_frontmatter(front), front, product_slug))
        return artifacts

    # --- selection ----------------------------------------------------------
    def select(self, unit_id: str | None, kinds: set[str] | None) -> list[Artifact]:
        targets = self.discover()
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
            for candidate in self.discover()
            if candidate.kind == "story"
            and candidate.product_slug == feature.product_slug
            and str(candidate.fields.get("parent_feature")) == feature.artifact_id
        ]

    def child_features(self, epic: Artifact) -> list[Artifact]:
        return [
            candidate
            for candidate in self.discover()
            if candidate.kind == "feature" and str(candidate.fields.get("parent_epic")) == epic.artifact_id
        ]

    def parent_feature_of(self, story: Artifact) -> Artifact | None:
        parent_id = str(story.fields.get("parent_feature"))
        return next(
            (a for a in self.discover() if a.kind == "feature" and a.product_slug == story.product_slug and a.artifact_id == parent_id),
            None,
        )

    def parent_epic_of(self, feature: Artifact) -> Artifact | None:
        parent_id = str(feature.fields.get("parent_epic"))
        return next((a for a in self.discover() if a.kind == "epic" and a.artifact_id == parent_id), None)

    def priced_children(self, artifact: Artifact) -> list[Artifact]:
        if artifact.kind == "feature":
            return self.child_stories(artifact)
        if artifact.kind == "epic":
            return self.child_features(artifact)
        return []

    def resolve_dependency(self, dependency_id: str) -> list[Artifact]:
        return [artifact for artifact in self.discover() if artifact.artifact_id == dependency_id]

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
