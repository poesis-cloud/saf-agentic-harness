"""The typed view over the model profile catalog."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from config.model_profile import ModelProfile
from errors import ConfigurationError


@dataclass(frozen=True)
class ModelProfiles:
    """Score catalog models against a step's weighted capability demand.

    Spec (`workflow.conf.schema.json`, `capabilities`): the harness multiplies a step's
    weights against the model catalog's capabilities and picks the highest-scoring model.
    """

    profiles: Mapping[str, ModelProfile]

    def score_model(self, profile_slug: str, capability_weights: Mapping[str, float]) -> float:
        """Score one model as the sum of requested weights times its capability scores."""
        profile = self.profiles.get(profile_slug)
        if profile is None:
            raise ConfigurationError(
                "unknown-model-profile",
                f"Model catalog holds no profile for the model slug '{profile_slug}'.",
                False,
            )

        score = 0.0
        for tag, weight in capability_weights.items():
            capability = profile.capabilities.get(tag)
            if capability is None:
                raise ConfigurationError(
                    "unknown-capability-tag",
                    f"Model profile '{profile_slug}' scores no capability tag '{tag}'.",
                    False,
                )
            score += weight * capability
        return score


__all__ = ["ModelProfiles"]
