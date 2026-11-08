"""One artifact-plus-action privilege, as the ACL contract models it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Privilege:
    """Bind one artifact schema slug to one action verb.

    Spec (`config` package): a privilege is the modeled `artifact` + `action` pair the
    contract declares, never a flattened string. Frozen, therefore hashable, so grants
    are frozensets.
    """

    artifact: str
    action: str


__all__ = ["Privilege"]
