"""TransitionPolicy — status vocabularies, transition tables, gates, and ownership.

Loaded from config/transition-policy.yaml so the harness stays methodology-agnostic.
A different methodology can supply its own policy file without changing harness code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    yaml = None


class TransitionPolicy:
    """The status-transition policy for artifact kinds that have a lifecycle.
    
    Pure policy — no I/O during checks — loaded once from framework config.
    """

    def __init__(self, policy_path: Path | None = None) -> None:
        # __file__ = harness/src/services/transition_policy.py; parents[3] = framework root
        self.policy_path = policy_path or (Path(__file__).resolve().parents[3] / "config" / "transition-policy.yaml")
        self._data: dict[str, Any] | None = None
        self._statuses_by_kind: dict[str, set[str]] = {}
        self._transitions_by_kind: dict[str, set[tuple[str, str]]] = {}
        self._post_gate_by_kind: dict[str, set[str]] = {}
        self._gate_edges: dict[str, dict[tuple[str, str], str]] = {}
        self._reject_targets: dict[str, dict[tuple[str, str], str]] = {}
        self._deprecated_by_kind: dict[str, dict[str, str]] = {}
        self._orchestrator_kinds: dict[str, set[str]] = {}
        self._orchestrator_aliases: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if yaml is None:
            raise RuntimeError("PyYAML is required to load transition policy")
        if not self.policy_path.is_file():
            raise FileNotFoundError(f"transition policy not found: {self.policy_path}")
        data = yaml.safe_load(self.policy_path.read_text(encoding="utf-8")) or {}
        self._data = data

        kinds = data.get("kinds", {})
        for kind, cfg in kinds.items():
            self._statuses_by_kind[kind] = set(cfg.get("statuses", []))
            self._transitions_by_kind[kind] = {tuple(edge) for edge in cfg.get("transitions", [])}
            self._post_gate_by_kind[kind] = set(cfg.get("post_gate", []))
            self._deprecated_by_kind[kind] = dict(cfg.get("deprecated", {}))

            gates: dict[tuple[str, str], str] = {}
            for key, name in cfg.get("gates", {}).items():
                from_status, _, to_status = key.partition(" -> ")
                gates[(from_status.strip(), to_status.strip())] = name
            self._gate_edges[kind] = gates

            rejects: dict[tuple[str, str], str] = {}
            for key, target in cfg.get("reject_targets", {}).items():
                from_status, _, to_status = key.partition(" -> ")
                rejects[(from_status.strip(), to_status.strip())] = target
            self._reject_targets[kind] = rejects

        for orchestrator, kinds in data.get("orchestrators", {}).items():
            self._orchestrator_kinds[orchestrator] = set(kinds)
        self._orchestrator_aliases = dict(data.get("aliases", {}))

    # Backward-compatible public attributes (computed properties)
    @property
    def STATUSES_BY_KIND(self) -> dict[str, set[str]]:
        return self._statuses_by_kind

    @property
    def TRANSITIONS_BY_KIND(self) -> dict[str, set[tuple[str, str]]]:
        return self._transitions_by_kind

    @property
    def GATE_EDGES(self) -> dict[str, dict[tuple[str, str], str]]:
        return self._gate_edges

    @property
    def REJECT_TARGETS(self) -> dict[tuple[str, str, str], str]:
        """Flattened reject targets keyed by (kind, from_status, to_status)."""
        result: dict[tuple[str, str, str], str] = {}
        for kind, targets in self._reject_targets.items():
            for (from_status, to_status), target in targets.items():
                result[(kind, from_status, to_status)] = target
        return result

    @property
    def DEPRECATED_STATUSES_BY_KIND(self) -> dict[str, dict[str, str]]:
        return self._deprecated_by_kind

    @property
    def POST_GATE_BY_KIND(self) -> dict[str, set[str]]:
        return self._post_gate_by_kind

    @property
    def ORCHESTRATOR_KINDS(self) -> dict[str, set[str]]:
        return self._orchestrator_kinds

    @property
    def ORCHESTRATOR_ALIASES(self) -> dict[str, str]:
        return self._orchestrator_aliases

    def is_legal_edge(self, kind: str, from_status: str, to_status: str) -> bool:
        if to_status == "blocked":
            return from_status != "blocked"
        if from_status == "blocked":
            return to_status in self._statuses_by_kind.get(kind, set())
        return (from_status, to_status) in self._transitions_by_kind.get(kind, set())

    def gate_for_edge(self, kind: str, from_status: str, to_status: str) -> str | None:
        return self._gate_edges.get(kind, {}).get((from_status, to_status))
