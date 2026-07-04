"""AuthorizationPolicy — answer whole-resource authorization from the validated ACL config.

The harness is methodology-agnostic: this service knows nothing about concrete SAFe artifacts,
agents, or roles. It consumes the validated :class:`config.AccessControlList` view (actors →
roles → privileges) and answers ``allows(actor, action, resource)``. The singleton path-to-kind
map comes from the workspace layout config (well-known single-instance files).
"""

from __future__ import annotations

import fnmatch

from config import AccessControlList


class AuthorizationPolicy:
    """The authorization plane: ``allows(actor, action, resource)`` over the ACL config.

    Grants are whole-resource RBAC keyed by artifact schema filename. The actor is an AGENT
    filename (the harness identifies the agent, not the skill)."""

    ACTIONS = {"create", "read", "update", "delete"}

    def __init__(
        self,
        acl: AccessControlList,
        singleton_path_kind: dict[str, str] | None = None,
    ) -> None:
        self.acl = acl
        self.singleton_path_kind = singleton_path_kind or {}

    @staticmethod
    def normalize(handle: str) -> str:
        return AccessControlList.normalize(handle)

    @staticmethod
    def _resource_aliases(resource: str) -> set[str]:
        aliases = {resource.strip()}
        if resource.endswith(".artifact.schema.json"):
            aliases.add(resource[: -len(".artifact.schema.json")])
        else:
            aliases.add(f"{resource}.artifact.schema.json")
        return aliases

    def agents(self) -> dict[str, set[str]]:
        return self.acl.agents()

    def singleton_kind(self, path: str) -> str | None:
        for pattern, kind in self.singleton_path_kind.items():
            if fnmatch.fnmatch(path, pattern):
                return kind
        return None

    def allows(self, actor: str, action: str, resource: str) -> bool:
        perms = self.acl.privileges(actor)
        if not perms:
            return False
        needed = self._resource_aliases(resource)
        normalized_action = str(action or "").strip().upper()
        for artifact in perms.get("artifacts", set()):
            if self._resource_aliases(artifact).intersection(needed):
                if normalized_action in perms["actions"]:
                    return True
        return False


__all__ = ["AuthorizationPolicy"]
