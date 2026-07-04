"""Model-routing TEST — the harness ModelRouter resolves deterministically from configuration.

The router consumes two static, contract-validated configuration layers: the workflow step's
weighted ``capabilities`` map and the model catalog (``conf/model-profiles.conf.yaml``). The score
is the pure weighted capability sum; ties break toward lower ``cost_rank``; all-zero weights are
unroutable (the caller halts). Run via ``make verify``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import FrameworkConfig, ModelProfiles
from mappers import Workspace
from services import ModelRouter


def _router() -> ModelRouter:
    return ModelRouter(FrameworkConfig.detect(Workspace.detect()).model_profiles)


def test_resolves_highest_weighted_score() -> None:
    router = _router()
    resolved = router.resolve({"deep-reasoning": 9})
    assert resolved is not None
    # the winner's score must dominate every other candidate's.
    best = resolved["model"]
    for model in router.profiles.models():
        assert router.score(best, {"deep-reasoning": 9}) >= router.score(model, {"deep-reasoning": 9})
    assert "deep-reasoning" in resolved["reason"]


def test_all_zero_weights_is_unroutable() -> None:
    # a human gate: all weights zero -> no dispatch target; the caller halts.
    assert _router().resolve({"deep-reasoning": 0, "coding": 0}) is None
    assert _router().resolve({}) is None


def test_tie_breaks_toward_lower_cost() -> None:
    profiles = ModelProfiles({"models": [
        {"id": "pricey", "cost_rank": 9, "capability_scores": {"coding": 7}},
        {"id": "cheap", "cost_rank": 2, "capability_scores": {"coding": 7}},
    ]})
    resolved = ModelRouter(profiles).resolve({"coding": 5})
    assert resolved is not None
    assert resolved["model"] == "cheap"


def test_score_is_weighted_sum() -> None:
    profiles = ModelProfiles({"models": [
        {"id": "m", "cost_rank": 5, "capability_scores": {"coding": 8, "deep-reasoning": 6}},
    ]})
    router = ModelRouter(profiles)
    # 8*3 + 6*2 = 36; a tag absent from the step's map contributes nothing.
    assert router.score("m", {"coding": 3, "deep-reasoning": 2}) == 36.0
    assert router.score("m", {"coding": 3}) == 24.0


def test_empty_catalog_is_unroutable() -> None:
    assert ModelRouter(ModelProfiles({"models": []})).resolve({"coding": 5}) is None


def test_known_model_validation() -> None:
    router = _router()
    assert router.validate_dispatch("any-agent", None) is not None
    assert router.validate_dispatch("any-agent", "Auto") is not None
    assert router.validate_dispatch("any-agent", "not-a-model") is not None
    known = next(iter(router.profiles.models()))
    assert router.validate_dispatch("any-agent", known) is None
