"""ModelRouter — the deterministic model-routing resolver (harness-owned LLM routing engine).

The orchestrator agents speak only TIERS + CAPABILITY TAGS + DISPATCH KNOBS; the concrete
``Model (Vendor)`` strings, capability scores, and dispatch-profile metadata live in the harness map
``llm/map.yaml``. This service encodes the scoring + selection that used to be prose in
the orchestrator SKILL (filter by tier floor, score ``sum(capability_scores[tag]) - cost_rank *
cost_penalty``, pick the highest, resolve the config profile + dispatch knobs) so a dispatch's model
resolves *deterministically* and can be validated/injected by the hook — instead of the agent
re-deriving it. Risk/complexity classification stays the agent's judgment; this engine turns the
resulting (tier floor, tags, knobs) into one resolved model binding.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from persistence import Workspace


class ModelRouter:
    """Loads ``llm/map.yaml`` and resolves a concrete model key from a tier floor +
    capability tags, applying the cost-penalty scoring and the per-model dispatch-knob limits."""

    # risk -> tier floor (low defers to the role default); complexity -> tier floor (simple = no raise).
    _RISK_FLOOR = {"critical": "tier-high", "medium": "tier-balanced", "low": None}
    _COMPLEXITY_FLOOR = {"complex": "tier-high", "involved": "tier-balanced", "simple": None}

    def __init__(self, workspace: Workspace, path: Path | None = None) -> None:
        self.workspace = workspace
        self.path = path or (workspace.harness_dir / "llm" / "map.yaml")
        self._data: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            self._data = (yaml.safe_load(self.workspace.read_text(self.path)) or {}) if self.path.is_file() else {}
        return self._data

    def models(self) -> dict[str, Any]:
        return self._load().get("models") or {}

    def tiers(self) -> dict[str, str]:
        return self._load().get("tiers") or {}

    def _tier_rank(self, tier: str | None) -> int:
        return int((self._load().get("tier_rank") or {}).get(tier, 0))

    def _knob_rank(self, knob: str, level: str | None) -> int:
        table = "thinking_effort_rank" if knob == "thinking_effort" else "context_window_rank"
        return int((self._load().get(table) or {}).get(level, 0))

    def is_known_model(self, model: str) -> bool:
        return model in self.models()

    def role_default(self, agent: str) -> str:
        """The role's minimum dispatch tier (when risk is low); roles absent from the map floor at
        tier-fast (the lowest), so an unknown role is never over-blocked."""
        return (self._load().get("role_defaults") or {}).get(agent, "tier-fast")

    def validate_dispatch(self, agent: str, model: str | None) -> str | None:
        """Return an error string if a ``(agent, model)`` dispatch is off-policy, else None. Enforces:
        a resolved model is set (never Auto/omitted); it is a known routing key; and it clears the
        agent's role-default tier floor. Risk/complexity may raise the floor further — which the harness
        can't observe — so this is the MINIMUM gate, not the full resolution."""
        if not model or str(model).strip().lower() == "auto":
            return "no resolved model set (never pass Auto or omit model); resolve via llm/map.yaml"
        if not self.is_known_model(model):
            return f"model {model!r} is not a known routing key in llm/map.yaml"
        floor = self.role_default(agent)
        tier = (self.models().get(model) or {}).get("tier")
        if self._tier_rank(tier) < self._tier_rank(floor):
            return f"model {model!r} (tier {tier}) is below the role-default floor {floor!r} for agent {agent!r}"
        return None

    def tier_floor(self, risk: str, complexity: str, role_default: str = "tier-fast") -> str:
        """Final tier floor = max(risk floor, complexity floor, role default) by tier rank."""
        floors = [role_default, self._RISK_FLOOR.get(risk), self._COMPLEXITY_FLOOR.get(complexity)]
        return max((f for f in floors if f), key=self._tier_rank, default=role_default)

    def _resolve_knob(self, model_entry: dict[str, Any], knob: str, requested: str | None) -> str | None:
        spec = (model_entry.get("dispatch_knobs") or {}).get(knob) or {}
        supported = list(spec.get("supported") or [])
        default = spec.get("default")
        wanted = requested or (self._load().get("dispatch_defaults") or {}).get(knob) or default
        if wanted in supported:
            return wanted
        # downgrade to the highest supported level that does not exceed the request; else model default.
        wanted_rank = self._knob_rank(knob, wanted)
        below = [s for s in supported if self._knob_rank(knob, s) <= wanted_rank]
        if below:
            return max(below, key=lambda s: self._knob_rank(knob, s))
        return default

    def resolve(
        self,
        tier_floor: str,
        tags: list[str] | None = None,
        thinking_effort: str | None = None,
        context_window: str | None = None,
        config_profile: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve one model binding, or None to HALT (no candidate clears the floor). Returns
        ``{model, tier, config_profile, thinking_effort, context_window, reason}``."""
        data = self._load()
        tags = tags or []
        floor_rank = self._tier_rank(tier_floor)
        candidates = {key: entry for key, entry in self.models().items() if self._tier_rank(entry.get("tier")) >= floor_rank}
        if not candidates:
            return None

        if not tags:
            model = self.tiers().get(tier_floor) or max(candidates, key=lambda k: self._tier_rank(candidates[k].get("tier")))
            reason = "tier default (no capability tags)"
        else:
            penalty = float((data.get("scoring") or {}).get("cost_penalty", 0))

            def score(entry: dict[str, Any]) -> float:
                caps = entry.get("capability_scores") or {}
                return sum(float(caps.get(tag, 0)) for tag in tags) - float(entry.get("cost_rank", 0)) * penalty

            # highest score; ties -> lower cost_rank (the -cost_rank term).
            model = max(candidates, key=lambda k: (score(candidates[k]), -float(candidates[k].get("cost_rank", 0))))
            reason = f"scored on {', '.join(tags)}"

        entry = self.models().get(model) or {}
        profile = config_profile or data.get("default_config_profile")
        return {
            "model": model,
            "tier": entry.get("tier"),
            "config_profile": profile,
            "thinking_effort": self._resolve_knob(entry, "thinking_effort", thinking_effort),
            "context_window": self._resolve_knob(entry, "context_window", context_window),
            "reason": reason,
        }
