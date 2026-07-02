"""ArtifactChecker — the STATE plane: FSM + linkage + open-item rules + gate packets."""

from __future__ import annotations

from models import Artifact, Report
from mappers import ArtifactMapper, Workspace
from .schema_checker import SchemaChecker
from .transition_policy import TransitionPolicy


class ArtifactChecker:
    """Validates every Epic/Feature/Story's verifiable state: status FSM, parent linkage,
    blocking open_items across gates (`check_artifact_rules` / `check_all`), and staged
    gate-packet evidence (`check_gate_packet`). Schema conformance is delegated to the
    `SchemaChecker`; the artifact universe + relations come from the `ArtifactMapper`.
    """

    def __init__(self, workspace: Workspace, artifacts: ArtifactMapper, schema_checker: SchemaChecker, policy: TransitionPolicy) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.schema_checker = schema_checker
        self.policy = policy

    def check_artifact_rules(self, targets: list[Artifact]) -> Report:
        report = Report()
        universe = self.artifacts.scan_raw()
        portfolio_root = self.workspace.portfolio_root
        epic_ids = {artifact.artifact_id for artifact in universe if artifact.kind == "epic"}
        feature_ids = {(artifact.product_slug, artifact.artifact_id) for artifact in universe if artifact.kind == "feature"}

        for artifact in targets:
            label = self.workspace.label(artifact.path, portfolio_root)
            status = artifact.status
            if artifact.kind == "epic":
                allowed = self.policy.EPIC_STATUSES
                post_gate = self.policy.EPIC_POST_GATE
            elif artifact.kind == "feature":
                allowed = self.policy.FEATURE_STATUSES
                post_gate = self.policy.FEATURE_POST_GATE
            else:
                allowed = self.policy.STORY_STATUSES
                post_gate = self.policy.STORY_POST_GATE

            if not status:
                report.error(label, "missing status frontmatter")
                continue
            if artifact.kind == "feature" and status in self.policy.DEPRECATED_FEATURE_STATUSES:
                report.warn(label, f"deprecated Feature status {status!r}; use {self.policy.DEPRECATED_FEATURE_STATUSES[status]!r}")
            elif status not in allowed:
                report.error(label, f"invalid {artifact.kind} status {status!r}")

            if artifact.kind in {"feature", "story"}:
                product = artifact.fields.get("product")
                if product is None:
                    report.warn(label, "missing product frontmatter; path product is the only product signal")
                elif str(product) != artifact.product_slug:
                    report.warn(label, f"product frontmatter {product!r} does not match path product {artifact.product_slug!r}")

            if artifact.kind == "feature":
                parent_epic = artifact.fields.get("parent_epic")
                if parent_epic not in (None, "null") and str(parent_epic) not in epic_ids:
                    report.error(label, f"parent_epic {parent_epic!r} does not resolve to portfolio/epics")
                if status == "arch-pending" and not artifact.bool_field("structurant"):
                    report.error(label, "Feature is arch-pending but structurant is not true")

            if artifact.kind == "story":
                parent_feature = artifact.fields.get("parent_feature")
                if parent_feature not in (None, "null") and (artifact.product_slug, str(parent_feature)) not in feature_ids:
                    report.error(label, f"parent_feature {parent_feature!r} does not resolve in product {artifact.product_slug!r}")

            if status in post_gate and artifact.has_blocking_open_items():
                report.error(label, "blocking open_items entry remains open after a gate boundary")

        return report

    def check_all(self) -> Report:
        report = Report()
        portfolio_root = self.workspace.portfolio_root
        if not portfolio_root.is_dir():
            report.error(portfolio_root, "portfolio root does not exist")
            return report

        artifacts = self.artifacts.scan_raw()
        if not artifacts:
            report.warn(portfolio_root, "no Epic, Feature, or Story artifacts found")
        report.extend(self.check_artifact_rules(artifacts))
        report.extend(self.schema_checker.conformance(artifacts))
        return report

    def check_target(self, unit_id: str, kinds: set[str] | None) -> tuple[Report, list[Artifact]]:
        report = Report()
        portfolio_root = self.workspace.portfolio_root
        if not portfolio_root.is_dir():
            report.error(portfolio_root, "portfolio root does not exist")
            return report, []

        targets = self.artifacts.select(unit_id, kinds)
        if not targets:
            kind_suffix = f" for kinds {sorted(kinds)}" if kinds else ""
            report.error(portfolio_root, f"no artifact found with id {unit_id!r}{kind_suffix}")
            return report, []
        if len(targets) > 1:
            locations = sorted(str(target.product_slug or "portfolio") for target in targets)
            report.error(portfolio_root, f"unit id {unit_id!r} is not globally unique (found in {locations}); ids must be unique — rename to a unique slug")
            return report, targets

        report.extend(self.check_artifact_rules(targets))
        report.extend(self.schema_checker.conformance(targets))
        return report, targets

    def check_gate_packet(self, unit_id: str | None) -> Report:
        report = Report()
        portfolio_root = self.workspace.portfolio_root
        targets = self.artifacts.scan_raw()
        if unit_id is not None:
            targets = [artifact for artifact in targets if artifact.artifact_id == unit_id]
        if unit_id and not targets:
            report.error(portfolio_root, f"no artifact found with id {unit_id!r}")
            return report
        if unit_id and len(targets) > 1:
            locations = sorted(str(target.product_slug or "portfolio") for target in targets)
            report.error(portfolio_root, f"unit id {unit_id!r} is not globally unique (found in {locations}); ids must be unique — rename to a unique slug")
            return report

        for artifact in targets:
            label = self.workspace.label(artifact.path, portfolio_root)
            status = artifact.status
            if artifact.has_blocking_open_items():
                report.error(label, "gate packet has blocking open_items entry still open")

            if artifact.kind == "story" and status == "awaiting-pr":
                if not self.artifacts.find_qa_signoff(artifact):
                    report.error(label, "PR Gate packet is missing qa/<story>-signoff.md evidence")
                if artifact.fields.get("pr") in (None, "") and artifact.fields.get("github") in (None, ""):
                    report.warn(label, "PR Gate packet has no pr or github frontmatter reference")

            if artifact.kind == "feature" and status in {"arch-pending", "ready", "committed", "in-progress", "done"}:
                if artifact.bool_field("structurant"):
                    inventory_value = artifact.fields.get("architecture_inventory")
                    if inventory_value in (None, "null", ""):
                        report.error(label, "Architecture Gate packet is missing architecture_inventory")
                    else:
                        inventory_path = self.artifacts.product_root(artifact) / str(inventory_value)
                        if not inventory_path.is_file():
                            report.error(label, f"architecture_inventory does not exist: {inventory_value}")
                    adrs = artifact.list_field("adrs")
                    if not adrs:
                        report.warn(label, "structurant Feature has no adrs[] references; inventory must carry an explicit waiver")
                    for adr_id in adrs:
                        if self.artifacts.find_adr(self.artifacts.product_root(artifact), adr_id) is None:
                            report.error(label, f"ADR reference {adr_id!r} does not resolve in product architecture/")

            if artifact.kind == "epic" and status in {"analyzing", "portfolio-backlog"}:
                if not artifact.list_field("products"):
                    report.error(label, "Epic Gate packet is missing products[]")
                if artifact.fields.get("strategic_theme") in (None, ""):
                    report.error(label, "Epic Gate packet is missing strategic_theme")

        return report
