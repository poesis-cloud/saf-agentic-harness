"""ArtifactChecker — the STATE plane: status FSM, linkage, and open-item rules.

Generic harness checker. Status vocabularies and transition tables are loaded from
config/transition-policy.yaml. Framework-specific linkage and gate-packet evidence rules
(e.g. parent/child refs, architecture inventory, QA signoff) are intentionally removed from
the harness core; they should be expressed as workflow postconditions (CEL state conditions)
so the harness evaluates them generically.
"""

from __future__ import annotations

from models import Artifact, Report
from mappers import ArtifactMapper, Workspace
from .schema_checker import SchemaChecker
from .transition_policy import TransitionPolicy


class ArtifactChecker:
    """Validates an artifact's verifiable state: status FSM, parent linkage, and blocking
    open_items across gates. Schema conformance is delegated to the `SchemaChecker`; the
    artifact universe + relations come from the `ArtifactMapper`.
    """

    def __init__(self, workspace: Workspace, artifacts: ArtifactMapper, schema_checker: SchemaChecker, policy: TransitionPolicy) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.schema_checker = schema_checker
        self.policy = policy

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
            status = artifact.status
            allowed = self.policy.STATUSES_BY_KIND.get(artifact.kind)
            post_gate = self.policy.STATUSES_BY_KIND.get(artifact.kind)  # generic: post-gate set loaded from policy

            if not status:
                report.error(label, "missing status frontmatter")
                continue
            if allowed is None:
                report.error(label, f"no transition policy for kind {artifact.kind!r}")
                continue

            deprecated = self.policy.DEPRECATED_STATUSES_BY_KIND.get(artifact.kind, {})
            if status in deprecated:
                report.warn(label, f"deprecated {artifact.kind} status {status!r}; use {deprecated[status]!r}")
            elif status not in allowed:
                report.error(label, f"invalid {artifact.kind} status {status!r}")

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

            # Generic post-gate open_items check.
            if status in allowed and artifact.has_blocking_open_items():
                report.error(label, "blocking open_items entry remains open after a gate boundary")

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

    def check_gate_packet(self, unit_id: str | None) -> Report:
        """Generic gate-packet check: only blocking open_items are validated here.
        Methodology-specific gate-packet evidence (QA signoff, architecture inventory,
        strategic themes, etc.) should be expressed as workflow postconditions.
        """
        report = Report()
        workspace_root = self.workspace.workspace_root
        targets = self.artifacts.scan_raw()
        if unit_id is not None:
            targets = [artifact for artifact in targets if artifact.artifact_id == unit_id]
        if unit_id and not targets:
            report.error(workspace_root, f"no artifact found with id {unit_id!r}")
            return report
        if unit_id and len(targets) > 1:
            locations = sorted(str(target.scope_slug or "workspace") for target in targets)
            report.error(workspace_root, f"unit id {unit_id!r} is not globally unique (found in {locations}); ids must be unique — rename to a unique slug")
            return report

        for artifact in targets:
            label = self.workspace.label(artifact.path, workspace_root)
            if artifact.has_blocking_open_items():
                report.error(label, "gate packet has blocking open_items entry still open")

        return report

    def check_transition(self, unit_id: str, to_status: str, gate: str | None, orchestrator: str | None) -> Report:
        """Evaluate one status edge against the deterministic transition policy."""
        report = Report()
        workspace_root = self.workspace.workspace_root
        if not workspace_root.is_dir():
            report.error(workspace_root, "workspace root does not exist")
            return report

        targets = self.artifacts.select(unit_id, None)
        if not targets:
            report.error(workspace_root, f"no artifact found with id {unit_id!r}")
            return report
        if not self.artifacts.ambiguity_error(report, targets, unit_id):
            return report

        artifact = targets[0]
        kind = artifact.kind
        label = self.workspace.label(artifact.path, workspace_root)
        from_status = artifact.status
        if from_status is None:
            report.error(label, "artifact has no status to transition from")
            return report

        allowed = self.policy.STATUSES_BY_KIND.get(kind)
        if allowed is None:
            report.error(label, f"no transition policy for kind {kind!r}")
            return report
        if to_status not in allowed:
            report.error(label, f"{to_status!r} is not a valid {kind} status")
            return report
        if from_status == to_status:
            report.warn(label, f"already in status {to_status!r}; nothing to do")
            return report
        if not self.policy.is_legal_edge(kind, from_status, to_status):
            report.error(label, f"illegal {kind} transition {from_status!r} -> {to_status!r}")
            return report

        if orchestrator is not None:
            canonical = self.policy.ORCHESTRATOR_ALIASES.get(orchestrator, orchestrator)
            if kind not in self.policy.ORCHESTRATOR_KINDS.get(canonical, set()):
                report.error(label, f"orchestrator {canonical!r} does not own {kind} transitions")
                return report

        gate_name = self.policy.gate_for_edge(kind, from_status, to_status)
        if gate_name is not None:
            if artifact.has_blocking_open_items():
                report.error(label, f"cannot cross the {gate_name}: a blocking open_items entry is still open")
                return report
            if gate == "reject":
                target = self.policy.REJECT_TARGETS.get((kind, from_status, to_status))
                hint = f"; route back with --to {target}" if target else ""
                report.warn(label, f"{gate_name} rejected{hint}")
                return report
            if gate != "accept":
                report.error(
                    label,
                    f"{from_status} -> {to_status} crosses the {gate_name} (a human decision); re-run with --gate accept after the supervisor decides, or --gate reject to route back",
                )
                return report

        accepted = f" [{gate_name} accepted]" if gate_name else ""
        print(f"OK to commit {label}: {from_status} -> {to_status}{accepted} — the orchestrator writes status:")
        return report
