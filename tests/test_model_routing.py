"""Model-routing TEST — the harness ModelRouter resolves deterministically from the routing map.

DESIGN-TIME framework test for ``ModelRouter`` over ``llm/map.yaml``: the tier-floor
mapping, tier-default selection when no capability tags are given, capability-score + cost-penalty
selection, dispatch-knob downgrade against a model's supported levels, and known-model validation.
Run via ``make verify``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persistence import Workspace
from services import ModelRouter


def _router() -> ModelRouter:
    return ModelRouter(Workspace.detect())


def test_tier_floor_mapping() -> None:
    router = _router()
    assert router.tier_floor("critical", "simple") == "tier-high"
    assert router.tier_floor("medium", "complex") == "tier-high"      # complexity raises above medium
    assert router.tier_floor("medium", "simple") == "tier-balanced"
    assert router.tier_floor("low", "simple", "tier-fast") == "tier-fast"


def test_no_tags_uses_tier_default() -> None:
    router = _router()
    resolved = router.resolve("tier-high", [])
    assert resolved is not None
    assert resolved["model"] == router.tiers()["tier-high"]
    assert resolved["reason"].startswith("tier default")


def test_capability_scoring_prefers_stronger_model() -> None:
    # deep-reasoning from a tier-balanced floor: GPT-5.5 (5 - 4*0.35=3.6) beats GPT-5.4 (4 - 2*0.35=3.3).
    resolved = _router().resolve("tier-balanced", ["deep-reasoning"])
    assert resolved is not None
    assert resolved["model"] == "GPT-5.5 (copilot)"
    assert "deep-reasoning" in resolved["reason"]


def test_dispatch_knob_downgrades_to_supported() -> None:
    # The tier-fast default model does not support thinking_effort=high; it must downgrade, never exceed.
    router = _router()
    resolved = router.resolve("tier-fast", [], thinking_effort="high")
    assert resolved is not None
    entry = router.models()[resolved["model"]]
    supported = entry["dispatch_knobs"]["thinking_effort"]["supported"]
    assert resolved["thinking_effort"] in supported
    assert "high" not in supported  # precondition for this test's meaning


def test_default_config_profile() -> None:
    resolved = _router().resolve("tier-balanced", ["structured-output"])
    assert resolved is not None
    assert resolved["config_profile"] == "deterministic"


def test_known_model_validation() -> None:
    router = _router()
    assert router.is_known_model("GPT-5.5 (copilot)") is True
    assert router.is_known_model("Auto") is False
