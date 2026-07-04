"""AccessControlList — the typed view over conf/access-control-list.conf.yaml.

Whole-resource RBAC data: actors (agent files) hold roles; roles carry privileges of one artifact
plus one action verb. This class only exposes the configured facts; answering `allows(actor,
action, resource)` is the `AuthorizationPolicy` service's job.
"""

from __future__ import annotations

import os
from typing import Any


class AccessControlList:
    """Typed accessors over the validated ACL document."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self._agent_privileges: dict[str, dict[str, set[str]]] | None = None

    @staticmethod
    def normalize(handle: str) -> str:
        normalized = os.path.basename(handle.strip().lstrip("@"))
        if normalized.endswith(".agent.md"):
            normalized = normalized[: -len(".agent.md")]
        return normalized

    def _load(self) -> dict[str, dict[str, set[str]]]:
        if self._agent_privileges is not None:
            return self._agent_privileges
        privileges: dict[str, dict[str, set[str]]] = {}
        roles: dict[str, list[dict[str, str]]] = {}
        for role in self._data.get("roles", []):
            if isinstance(role, dict) and role.get("id"):
                roles[role["id"]] = role.get("privileges", [])
        for actor in self._data.get("actors", []):
            if not isinstance(actor, dict):
                continue
            agent = self.normalize(str(actor.get("id") or ""))
            for role_id in actor.get("roles", []):
                for priv in roles.get(role_id, []):
                    if not isinstance(priv, dict):
                        continue
                    artifact = str(priv.get("artifact") or "").strip()
                    action = str(priv.get("action") or "").strip().upper()
                    if agent and artifact:
                        entry = privileges.setdefault(agent, {"artifacts": set(), "actions": set()})
                        entry["artifacts"].add(artifact)
                        if action:
                            entry["actions"].add(action)
        self._agent_privileges = privileges
        return privileges

    def agents(self) -> dict[str, set[str]]:
        """agent -> the artifact resources it holds any privilege on."""
        return {agent: perms["artifacts"] for agent, perms in self._load().items()}

    def privileges(self, actor: str) -> dict[str, set[str]] | None:
        """The {'artifacts': set, 'actions': set} privilege entry for a normalized actor, or None."""
        return self._load().get(self.normalize(actor))


__all__ = ["AccessControlList"]
