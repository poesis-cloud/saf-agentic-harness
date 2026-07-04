"""ArtifactChecker — the STATE plane: linkage rules.

Generic harness checker. Framework-specific linkage and gate-packet evidence rules
(e.g. parent/child refs, architecture inventory, QA signoff) are intentionally removed from
the harness core; they should be expressed as workflow postconditions (CEL state conditions)
so the harness evaluates them generically.
"""

from __future__ import annotations

from models import Artifact, Report
from mappers import ArtifactMapper, Workspace
from .schema_checker import SchemaChecker


class ArtifactChecker:
    """Validates an artifact's verifiable state: parent linkage.
    Schema conformance is delegated to the `SchemaChecker`; the artifact universe + relations
    come from the `ArtifactMapper`.
    """

    def __init__(self, workspace: Workspace, artifacts: ArtifactMapper, schema_checker: SchemaChecker) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.schema_checker = schema_checker

    def check_artifact_rules(self, targets: list[Artifact]) -> Report:
        report = Report()
        universe = self.artifacts.scan_raw()
        workspace_root = self.workspace.workspace_root

        # Build id indexes for generic parent-linkage validation.
        ids_by_kind: dict[str, set[str]] = {}
        for artifact in universe:
            ids_by_kind.setdefault(artifact.kind, set()).add(artifact.artifact_id)

        for artifact in targets:
            label = self.workspace.label(artifact.path, workspace_root)

            # Generic scope/frontmatter coherence check (only when artifact is scoped).
            if artifact.scope_slug is not None:
                product = artifact.fields.get("product")
                if product is None:
                    report.warn(label, "missing product frontmatter; path scope is the only product signal")
                elif str(product) != artifact.scope_slug:
                    report.warn(label, f"product frontmatter {product!r} does not match path scope {artifact.scope_slug!r}")

            # Generic parent linkage: any declared parent_* field must resolve to an artifact
            # of the corresponding kind somewhere in the workspace. The field naming convention
            # (parent_<kind>) is framework-agnostic; scope boundaries are not enforced because
            # methodology-specific parent/child links may cross scopes.
            for field, value in artifact.fields.items():
                if field.startswith("parent_") and value not in (None, "null"):
                    parent_kind = field[len("parent_"):]
                    parent_id = str(value)
                    if parent_id not in ids_by_kind.get(parent_kind, set()):
                        report.error(label, f"{field} {parent_id!r} does not resolve")

        return report

    def check_all(self) -> Report:
        report = Report()
        workspace_root = self.workspace.workspace_root
        if not workspace_root.is_dir():
            report.error(workspace_root, "workspace root does not exist")
            return report

        artifacts = self.artifacts.scan_raw()
        if not artifacts:
            report.warn(workspace_root, "no artifacts found")
        report.extend(self.check_artifact_rules(artifacts))
        report.extend(self.schema_checker.conformance(artifacts))
        return report

    def check_target(self, unit_id: str, kinds: set[str] | None) -> tuple[Report, list[Artifact]]:
        report = Report()
        workspace_root = self.workspace.workspace_root
        if not workspace_root.is_dir():
            report.error(workspace_root, "workspace root does not exist")
            return report, []

        targets = self.artifacts.select(unit_id, kinds)
        if not targets:
            kind_suffix = f" for kinds {sorted(kinds)}" if kinds else ""
            report.error(workspace_root, f"no artifact found with id {unit_id!r}{kind_suffix}")
            return report, []
        if len(targets) > 1:
            locations = sorted(str(target.scope_slug or "workspace") for target in targets)
            report.error(workspace_root, f"unit id {unit_id!r} is not globally unique (found in {locations}); ids must be unique — rename to a unique slug")
            return report, targets

        report.extend(self.check_artifact_rules(targets))
        report.extend(self.schema_checker.conformance(targets))
        return report, targets

