"""The model resolution result: the report function 4 returns and journals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.model_resolution.model_profile_binding import ModelProfileBinding
from stores.session_log_store import Report


@dataclass(frozen=True)
class ModelProfileReport(Report):
    """Carry function 4's outcome and, on a resolution, the bound model profile.

    Spec (function 4, rule 2): `not-applicable` carries no function-specific payload,
    so the `profile` property is absent unless a profile actually bound.
    """

    profile: ModelProfileBinding | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the contract report object, attaching the profile when one bound."""
        rendered = super().to_dict()
        if self.profile is not None:
            rendered["profile"] = self.profile.to_dict()
        return rendered


__all__ = ["ModelProfileReport"]
