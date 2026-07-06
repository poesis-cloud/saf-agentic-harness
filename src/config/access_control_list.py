"""AccessControlList — the typed view over conf/access-control-list.conf.yaml.

Whole-resource RBAC: actors (agent files) hold roles; roles carry privileges of one artifact
plus one action verb. This view owns the authorization query itself — ``allows(actor, action,
resource)`` — since it is a pure function of the configured facts (resolving a write path to its
resource is the AuthorizationChecker's job; answering the grant question is this view's).
"""

from __future__ import annotations

import os
from typing import Any


class AccessControlList:
    """Typed accessors + the authorization query over the validated ACL document."""

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

    # --- the authorization query ---------------------------------------------
    @staticmethod
    def _resource_aliases(resource: str) -> set[str]:
        """A resource is granted under its schema filename OR its stem — both alias one grant."""
        aliases = {resource.strip()}
        if resource.endswith(".artifact.schema.json"):
            aliases.add(resource[: -len(".artifact.schema.json")])
        else:
            aliases.add(f"{resource}.artifact.schema.json")
        return aliases

    def allows(self, actor: str, action: str, resource: str) -> bool:
        """Whole-resource RBAC: does the actor hold the action verb on the resource (under any
        of its aliases)? Unknown actors and ungranted verbs/resources are denied."""
        perms = self.privileges(actor)
        if not perms:
            return False
        needed = self._resource_aliases(resource)
        normalized_action = str(action or "").strip().upper()
        for artifact in perms.get("artifacts", set()):
            if self._resource_aliases(artifact).intersection(needed):
                if normalized_action in perms["actions"]:
                    return True
        return False


__all__ = ["AccessControlList"]
