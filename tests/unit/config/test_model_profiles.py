"""Unit tests for model profile configuration views."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from errors import ConfigurationError
from tests.unit.config.conftest import model_profiles_yaml, write_yaml


class TestModelProfile:
    """Verify one model profile typed view."""

    def test_is_frozen_with_read_only_capabilities(self, config_loader, framework_root) -> None:
        """Spec: model profiles expose immutable capability scores."""
        write_yaml(framework_root / "conf" / "model-profiles.conf.yaml", model_profiles_yaml())

        profile = config_loader.load_model_profiles(framework_root).profiles["fast-coder"]

        assert profile.slug == "fast-coder"
        assert profile.cost_rank == 2
        with pytest.raises(TypeError):
            profile.capabilities["coding"] = 9  # type: ignore[index]
        with pytest.raises(FrozenInstanceError):
            profile.cost_rank = 3  # type: ignore[misc]


class TestModelProfiles:
    """Verify model profile catalog behavior."""

    def test_scores_model_by_weighted_capabilities(self, config_loader, framework_root) -> None:
        """Spec: score_model multiplies requested capability weights by profile scores."""
        write_yaml(framework_root / "conf" / "model-profiles.conf.yaml", model_profiles_yaml())

        profiles = config_loader.load_model_profiles(framework_root)

        assert profiles.score_model("fast-coder", {"coding": 2, "tool-use": 3}) == 2.0

    def test_rejects_duplicate_profile_slugs(self, config_loader, framework_root) -> None:
        """Spec: model profile slugs are catalog identities and must be unique."""
        write_yaml(
            framework_root / "conf" / "model-profiles.conf.yaml",
            model_profiles_yaml() + model_profiles_yaml().replace("modelProfiles:\n", ""),
        )

        with pytest.raises(ConfigurationError, match="duplicate model profile slug"):
            config_loader.load_model_profiles(framework_root)
