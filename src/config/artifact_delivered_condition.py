"""The harness-defined postcondition every step carries: its artifact was delivered."""

from __future__ import annotations

from types import MappingProxyType

from config.state_condition import StateCondition

ARTIFACT_DELIVERED_SLUG = "harness-artifact-delivered"

# Spec (function 10, invariant 5): no valid step without its artifact. The step's
# declared artifact kind must hold an instance whose harness-derived provenance names
# a session that executed this step — agent reports are never evidence.
ARTIFACT_DELIVERED_CONDITION = StateCondition(
    kind="postcondition",
    slug=ARTIFACT_DELIVERED_SLUG,
    set_selector=MappingProxyType(
        {
            "setQuery": (
                "artifacts[artifact].filter(a, a.provenance.sessionId in stepSessionIds)"
            )
        }
    ),
    set_predicate="selected.size() > 0",
)


__all__ = ["ARTIFACT_DELIVERED_CONDITION", "ARTIFACT_DELIVERED_SLUG"]
