"""AuthorizationPolicy — load access rules and answer whole-resource write authorization."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

import yaml


class AuthorizationPolicy:
    """The authorization plane. Loads ``config/access-control-list.yaml`` and answers
    ``allows(actor, action, resource)`` where ACL entries use:

    ``access: [{agent: <agent filename>, artifact: <schema filename>}, ...]``

    Grants are whole-resource RBAC. The ``action`` argument is kept for checker compatibility, but
    access is artifact-based.

    The actor is an AGENT (the harness identifies the agent, not the skill). Owner-only singleton
    paths still map to a kind so the highest-authority files resolve to the grant that protects them
    (e.g. the workspace singleton -> workspace-init -> VMO)."""

    ACTIONS = {"create", "read", "update", "delete"}

    # Owner-only singletons by workspace-root-relative glob -> the artifact kind that gates them.
    # The harness is methodology-agnostic: paths are relative to the workspace root, whatever its
    # on-disk name (workspace/, portfolio/, etc.).
    SINGLETON_PATH_KIND: dict[str, str] = {
        "portfolio-manifest.yaml": "portfolio-manifest",
        "_registry.yaml": "portfolio-manifest",
        "strategic-themes.md": "strategic-themes",
        "portfolio-vision.md": "portfolio-vision",
        "portfolio-roadmap.md": "portfolio-roadmap",
        "art/*/art-manifest.yaml": "art-manifest",
        "art/*/teams/*/team-manifest.yaml": "team-manifest",
        "products/*/product-manifest.yaml": "product-manifest",
    }

    def __init__(self, acl_path: Path | None = None) -> None:
        # __file__ = harness/src/services/authorization_policy.py; parents[3] = the framework
        # root. Env-agnostic config lives under config/.
        self.acl_path = acl_path or (Path(__file__).resolve().parents[3] / "config" / "access-control-list.yaml")
        self._agents: dict[str, set[str]] | None = None

    @staticmethod
    def normalize(handle: str) -> str:
        normalized = os.path.basename(handle.strip().lstrip("@"))
        if normalized.endswith(".agent.md"):
            normalized = normalized[: -len(".agent.md")]
        return normalized

    @staticmethod
    def _resource_aliases(resource: str) -> set[str]:
        aliases = {resource.strip()}
        if resource.endswith(".artifact.schema.json"):
            aliases.add(resource[: -len(".artifact.schema.json")])
        else:
            aliases.add(f"{resource}.artifact.schema.json")
        return aliases

    def agents(self) -> dict[str, set[str]]:
        if self._agents is None:
            data = yaml.safe_load(self.acl_path.read_text(encoding="utf-8")) if self.acl_path.is_file() else {}
            access = (data or {}).get("access", [])
            grouped: dict[str, set[str]] = {}
            for item in access or []:
                if not isinstance(item, dict):
                    continue
                agent = self.normalize(str(item.get("agent") or ""))
                artifact = str(item.get("artifact") or "").strip()
                if not agent or not artifact:
                    continue
                grouped.setdefault(agent, set()).add(artifact)
            self._agents = grouped
        return self._agents

    def singleton_kind(self, path: str) -> str | None:
        for pattern, kind in self.SINGLETON_PATH_KIND.items():
            if fnmatch.fnmatch(path, pattern):
                return kind
        return None

    def allows(self, actor: str, action: str, resource: str) -> bool:
        artifacts = self.agents().get(self.normalize(actor))
        if not artifacts:
            return False
        needed = self._resource_aliases(resource)
        for artifact in artifacts:
            if self._resource_aliases(artifact).intersection(needed):
                return True
        return False
