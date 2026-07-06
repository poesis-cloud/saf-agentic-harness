"""ModelRouter — the deterministic model-routing resolver (harness-owned LLM routing engine).

Routing is resolved from two static configuration layers (no artifact reads, no per-instance
estimation): the workflow step's weighted ``capabilities`` map (tag -> 0-10 weight, authored in
``conf/workflows/*.workflow.conf.yaml``) and the model catalog (``conf/model-profiles.conf.yaml``:
0-10 ``capability_scores`` per tag + ``cost_rank``). The score of a candidate model is the pure
weighted capability sum::

    score(m) = sum(capability_scores[m][tag] * step.capabilities[tag] for tag in step.capabilities)

Highest score wins; ties break toward lower ``cost_rank``. A step whose capabilities are all zero
(e.g. a human gate) or an empty catalog is unroutable — the caller halts rather than passing Auto.
"""

from __future__ import annotations

from typing import Any

from config import ModelProfiles


class ModelRouter:
    """Resolves a concrete model from a step's weighted capabilities against the model catalog."""

    def __init__(self, profiles: ModelProfiles) -> None:
        self.profiles = profiles

    def is_known_model(self, model: str) -> bool:
        return self.profiles.is_known_model(model)

    def score(self, model: str, capabilities: dict[str, float]) -> float:
        """The weighted capability sum for one candidate. Tags missing from the catalog entry
        score 0; tags missing from the step's map contribute 0 (weights are explicit per step)."""
        scores = self.profiles.capability_scores(model)
        return sum(scores.get(tag, 0.0) * float(weight) for tag, weight in capabilities.items())

    def resolve(self, capabilities: dict[str, float]) -> dict[str, Any] | None:
        """Resolve one model binding from a step's weighted capabilities, or None to HALT
        (all-zero weights or an empty catalog). Returns ``{model, score, cost_rank, reason}``."""
        weighted = {tag: float(w) for tag, w in (capabilities or {}).items() if float(w) > 0}
        if not weighted:
            return None
        candidates = list(self.profiles.models())
        if not candidates:
            return None
        best = max(candidates, key=lambda m: (self.score(m, weighted), -self.profiles.cost_rank(m)))
        return {
            "model": best,
            "score": round(self.score(best, weighted), 4),
            "cost_rank": self.profiles.cost_rank(best),
            "reason": "scored on " + ", ".join(sorted(weighted)),
        }

    def validate_dispatch(self, model: str | None) -> str | None:
        """Return an error string if a host-proposed dispatch model is off-policy, else None: a
        resolved model must be set (never Auto/omitted) and be a known catalog id. This guards the
        HOOK plane, where the model arrives from the host payload — a binding produced by
        :meth:`resolve` is a catalog id by construction and needs no re-check."""
        if not model or str(model).strip().lower() == "auto":
            return "no resolved model set (never pass Auto or omit model); resolve from the step's capabilities via conf/model-profiles.conf.yaml"
        if not self.is_known_model(model):
            return f"model {model!r} is not a known catalog id in conf/model-profiles.conf.yaml"
        return None
