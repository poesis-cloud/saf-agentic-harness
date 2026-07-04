"""ModelProfiles — the typed view over conf/model-profiles.conf.yaml (the model catalog).

The canonical, host-agnostic model catalog: per model, a stable `id`, a `cost_rank` (1 cheapest ..
10 most expensive), and 0-10 `capability_scores` per capability tag. The catalog owns the
capability-tag vocabulary; workflow steps weight the same tags. `ModelRouter` consumes this view.
"""

from __future__ import annotations

from typing import Any


class ModelProfiles:
    """Typed accessors over the validated model catalog document."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._models: dict[str, dict[str, Any]] = {}
        for entry in data.get("models", []):
            if isinstance(entry, dict) and entry.get("id"):
                self._models[str(entry["id"])] = entry

    def models(self) -> dict[str, dict[str, Any]]:
        """model id -> catalog entry ({id, cost_rank, capability_scores, ...})."""
        return self._models

    def is_known_model(self, model: str) -> bool:
        return model in self._models

    def capability_scores(self, model: str) -> dict[str, float]:
        entry = self._models.get(model) or {}
        scores = entry.get("capability_scores") or {}
        return {str(tag): float(score) for tag, score in scores.items()}

    def cost_rank(self, model: str) -> float:
        entry = self._models.get(model) or {}
        return float(entry.get("cost_rank", 0))

    def tags(self) -> set[str]:
        """Every capability tag scored anywhere in the catalog (the effective vocabulary)."""
        tags: set[str] = set()
        for entry in self._models.values():
            tags.update(str(t) for t in (entry.get("capability_scores") or {}))
        return tags


__all__ = ["ModelProfiles"]
