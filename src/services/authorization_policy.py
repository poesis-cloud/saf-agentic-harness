"""AuthorizationPolicy — the privilege engine: load acl/map.yaml and decide who may act on what."""

from __future__ import annotations

import fnmatch
from pathlib import Path

import yaml


class AuthorizationPolicy:
    """The authorization plane. Loads the harness-owned ``acl/map.yaml`` (agent -> privileges) and
    answers ``allows(actor, action, resource)``. A privilege is ``"<action>_<resource>"`` where
    action ∈ create/read/update/delete/* and resource is an artifact schema name or ``*``. This is
    PLAIN RBAC — per action + per WHOLE resource, never per property: the host hook only sees the file
    path being written (whole-file), not which field changes, so property-level grants would be
    unenforceable. Each artifact has one author; status TRANSITIONS are governed by the transition
    guard (check-artifact --to) + the layer kanban, not a write grant.

    The actor is an AGENT (the harness identifies the agent, not the skill). Owner-only singleton
    paths still map to a kind so the highest-authority files resolve to the grant that protects them
    (e.g. the portfolio singleton -> portfolio-init -> VMO)."""

    ACTIONS = {"create", "read", "update", "delete"}

    # Owner-only singletons by repo-relative glob -> the artifact kind that gates them.
    SINGLETON_PATH_KIND: dict[str, str] = {
        "portfolio/portfolio.yaml": "portfolio-init",
        "portfolio/_registry.yaml": "portfolio-init",
        "portfolio/strategic-themes.md": "strategic-themes",
        "portfolio/*/product.yaml": "product-init",
    }

    def __init__(self, acl_path: Path | None = None) -> None:
        # __file__ = harness/src/services/authorization_policy.py; parents[2] = the harness project
        # dir that holds the data maps (map/ stays at the project root, not under src/).
        self.acl_path = acl_path or (Path(__file__).resolve().parents[2] / "acl" / "map.yaml")
        self._agents: dict[str, set[str]] | None = None

    @staticmethod
    def normalize(handle: str) -> str:
        return handle.strip().lstrip("@")

    def agents(self) -> dict[str, set[str]]:
        if self._agents is None:
            data = yaml.safe_load(self.acl_path.read_text(encoding="utf-8")) if self.acl_path.is_file() else {}
            raw = (data or {}).get("agents", {})
            self._agents = {self.normalize(agent): set(privs or []) for agent, privs in raw.items()}
        return self._agents

    def singleton_kind(self, path: str) -> str | None:
        for pattern, kind in self.SINGLETON_PATH_KIND.items():
            if fnmatch.fnmatch(path, pattern):
                return kind
        return None

    def allows(self, actor: str, action: str, resource: str) -> bool:
        privileges = self.agents().get(self.normalize(actor))
        if not privileges:
            return False
        for privilege in privileges:
            priv_action, _, priv_resource = privilege.partition("_")
            if not priv_resource:
                continue
            if priv_action not in ("*", action):
                continue
            if priv_resource in ("*", resource):
                return True
        return False
