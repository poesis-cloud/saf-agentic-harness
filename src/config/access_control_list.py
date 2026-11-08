"""The typed view over `conf/access-control-list.conf.yaml`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from config.privilege import Privilege

_NO_PRIVILEGES: frozenset[Privilege] = frozenset()


@dataclass(frozen=True)
class AccessControlList:
    """Answer which privileges a framework agent holds.

    Spec (function 8, invariant 1): the actor is the AGENT identity derived from the
    registered host session, so grants are keyed by agent slug with the actors' roles
    already resolved into their privilege sets.
    """

    grants: Mapping[str, frozenset[Privilege]]

    def is_framework_agent(self, actor_slug: str) -> bool:
        """Tell whether the ACL declares this agent as a framework actor."""
        return actor_slug in self.grants

    def list_privileges(self, actor_slug: str) -> frozenset[Privilege]:
        """List the privileges granted to one agent; an unknown agent holds none."""
        return self.grants.get(actor_slug, _NO_PRIVILEGES)


__all__ = ["AccessControlList"]
