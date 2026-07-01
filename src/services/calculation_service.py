"""CalculationService — read-only derived-field (wsjf / cost) and transition-edge checks.

The harness never mutates artifacts. These methods COMPUTE the correct derived value or
evaluate the transition guard and REPORT it (drift + the value to write, or OK-to-commit /
blocked); the orchestrator applies the write. A re-run confirms it.
"""

from __future__ import annotations

import re
from typing import Any

from models import Artifact, Report
from persistence import ArtifactRepository, Workspace
from text import parse_scalar
from .transition_policy import TransitionPolicy


class CalculationService:
    def __init__(self, workspace: Workspace, artifacts: ArtifactRepository, policy: TransitionPolicy) -> None:
        self.workspace = workspace
        self.artifacts = artifacts
        self.policy = policy

    # --- compute helpers ----------------------------------------------------
    @staticmethod
    def coerce_number(value: Any) -> float:
        if isinstance(value, bool):
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def format_number(value: float) -> str:
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def block_scalar(block: list[str], key: str) -> Any:
        for line in block:
            match = re.match(rf"^\s+{re.escape(key)}:\s*(.*)$", line)
            if match is not None:
                return parse_scalar(match.group(1))
        return None

    @staticmethod
    def weakest_source(sources: list[Any]) -> str:
        cleaned = [str(source) for source in sources if source]
        if not cleaned:
            return "estimated"
        if all(source == "measured" for source in cleaned):
            return "measured"
        if all(source == "estimated" for source in cleaned):
            return "estimated"
        return "mixed"

    def compute_wsjf_score(self, artifact: Artifact) -> tuple[float | None, Any]:
        block = artifact.block("wsjf")
        if not block:
            return None, None
        stored = self.block_scalar(block, "score")
        job_size = self.coerce_number(self.block_scalar(block, "job_size"))
        if job_size == 0:
            return None, stored
        numerator = (
            self.coerce_number(self.block_scalar(block, "user_business_value"))
            + self.coerce_number(self.block_scalar(block, "time_criticality"))
            + self.coerce_number(self.block_scalar(block, "risk_reduction"))
        )
        return numerator / job_size, stored

    def cost_block(self, artifact: Artifact) -> dict[str, Any] | None:
        block = artifact.block("cost")
        if not block:
            return None
        return {
            "tokens_in": self.coerce_number(self.block_scalar(block, "tokens_in")),
            "tokens_out": self.coerce_number(self.block_scalar(block, "tokens_out")),
            "tokens_self": self.block_scalar(block, "tokens_self"),
            "tokens_rolled": self.block_scalar(block, "tokens_rolled"),
            "source": self.block_scalar(block, "source"),
            "committed": self.block_scalar(block, "committed"),
        }

    def rollup_suggestions(self, artifact: Artifact, to_status: str) -> list[str]:
        suggestions: list[str] = []
        if artifact.kind == "story" and to_status == "ready":
            feature = self.artifacts.parent_feature_of(artifact)
            if feature is not None and feature.status == "committed":
                suggestions.append(f"first child Story ready -> consider transition {feature.artifact_id} committed -> in-progress")
        if artifact.kind == "feature" and to_status == "funnel":
            epic = self.artifacts.parent_epic_of(artifact)
            if epic is not None and epic.status == "portfolio-backlog":
                suggestions.append(f"first child Feature funnel -> consider transition {epic.artifact_id} portfolio-backlog -> implementing")
        if artifact.kind == "story" and to_status == "done":
            feature = self.artifacts.parent_feature_of(artifact)
            if feature is not None:
                siblings = self.artifacts.child_stories(feature)
                if siblings and all(s.artifact_id == artifact.artifact_id or s.status == "done" for s in siblings):
                    suggestions.append(f"all child Stories done -> {feature.artifact_id} is ready for its Demo Gate (in-progress -> done, human)")
        if artifact.kind == "feature" and to_status == "done":
            epic = self.artifacts.parent_epic_of(artifact)
            if epic is not None:
                siblings = self.artifacts.child_features(epic)
                if siblings and all(f.artifact_id == artifact.artifact_id or f.status == "done" for f in siblings):
                    suggestions.append(f"all child Features done -> {epic.artifact_id} is ready for its Epic Outcome Gate (implementing -> done, human)")
        return suggestions

    # --- read-only checks (the orchestrator writes the value/status) --------
    def wsjf(self, unit_id: str | None) -> Report:
        """Check wsjf.score against the computed (UBV+TC+RR)/JS and report the value to write."""
        report = Report()
        portfolio_root = self.workspace.portfolio_root
        if not portfolio_root.is_dir():
            report.error(portfolio_root, "portfolio root does not exist")
            return report
        targets = self.artifacts.select(unit_id, {"epic", "feature"})
        if not self.artifacts.ambiguity_error(report, targets, unit_id):
            return report
        for artifact in targets:
            label = self.workspace.label(artifact.path, portfolio_root)
            computed, stored = self.compute_wsjf_score(artifact)
            if computed is None:
                if artifact.block("wsjf"):
                    report.warn(label, "wsjf.job_size is 0; score cannot be computed")
                continue
            expected = self.format_number(computed)
            current = self.format_number(self.coerce_number(stored)) if stored is not None else None
            if current != expected:
                report.warn(label, f"wsjf.score is {current}; should be {expected} = (UBV+TC+RR)/JS — write {expected}")
        return report

    def roll_cost(self, unit_id: str | None) -> Report:
        """Check cost.tokens_self/tokens_rolled/source against the bottom-up roll-up and report drift."""
        report = Report()
        portfolio_root = self.workspace.portfolio_root
        if not portfolio_root.is_dir():
            report.error(portfolio_root, "portfolio root does not exist")
            return report
        targets = self.artifacts.select(unit_id, {"epic", "feature", "story"})
        if not self.artifacts.ambiguity_error(report, targets, unit_id):
            return report
        targets = sorted(targets, key=lambda artifact: {"story": 0, "feature": 1, "epic": 2}.get(artifact.kind, 9))
        for artifact in targets:
            label = self.workspace.label(artifact.path, portfolio_root)
            cost = self.cost_block(artifact)
            if cost is None:
                report.warn(label, "no cost: frontmatter block to roll up")
                continue
            if cost["committed"] is not None:
                continue  # committed snapshot is immutable — drift is intentional, not reported
            children_costs = [self.cost_block(child) for child in self.artifacts.priced_children(artifact)]
            children_costs = [child for child in children_costs if child is not None]
            self_expected = cost["tokens_in"] + cost["tokens_out"]
            rolled_expected = self_expected + sum(self.coerce_number(child["tokens_rolled"]) for child in children_costs)
            source_expected = self.weakest_source([cost["source"]] + [child["source"] for child in children_costs])
            if self.format_number(self.coerce_number(cost["tokens_self"])) != self.format_number(self_expected):
                report.warn(label, f"cost.tokens_self is {cost['tokens_self']}; should be {self.format_number(self_expected)} (tokens_in + tokens_out)")
            if self.format_number(self.coerce_number(cost["tokens_rolled"])) != self.format_number(rolled_expected):
                report.warn(label, f"cost.tokens_rolled is {cost['tokens_rolled']}; should be {self.format_number(rolled_expected)} (self + Σ children)")
            if (str(cost["source"]) if cost["source"] else "estimated") != source_expected:
                report.warn(label, f"cost.source is {cost['source']}; should be {source_expected} (weakest of self + children)")
        return report

    def transition(self, unit_id: str, to_status: str, gate: str | None, orchestrator: str | None) -> Report:
        """Evaluate one status edge against the deterministic guard and report OK-to-commit / blocked.

        The harness does not write `status:` — on a clean verdict the orchestrator commits the flip.
        """
        report = Report()
        portfolio_root = self.workspace.portfolio_root
        if not portfolio_root.is_dir():
            report.error(portfolio_root, "portfolio root does not exist")
            return report
        targets = self.artifacts.select(unit_id, None)
        if not targets:
            report.error(portfolio_root, f"no Epic, Feature, or Story found with id {unit_id!r}")
            return report
        if not self.artifacts.ambiguity_error(report, targets, unit_id):
            return report

        artifact = targets[0]
        kind = artifact.kind
        label = self.workspace.label(artifact.path, portfolio_root)
        from_status = artifact.status
        if from_status is None:
            report.error(label, "artifact has no status to transition from")
            return report
        if to_status not in self.policy.STATUSES_BY_KIND.get(kind, set()):
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

        if kind == "feature" and to_status == "arch-pending" and not artifact.bool_field("structurant"):
            report.error(label, "Feature -> arch-pending requires structurant: true")
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
                    f"{from_status} -> {to_status} crosses the {gate_name} (a human decision); re-run with --gate accept after the Central Supervisor decides, or --gate reject to route back",
                )
                return report

        accepted = f" [{gate_name} accepted]" if gate_name else ""
        print(f"OK to commit {label}: {from_status} -> {to_status}{accepted} — the orchestrator writes status:")
        for suggestion in self.rollup_suggestions(artifact, to_status):
            print(f"  roll-up: {suggestion}")
        return report
