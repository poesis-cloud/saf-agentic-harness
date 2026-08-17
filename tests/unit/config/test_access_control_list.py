"""Unit tests for ACL configuration views."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from errors import ConfigurationError
from tests.unit.config.conftest import write_yaml


class TestPrivilege:
    """Verify modeled artifact/action privileges."""

    def test_is_hashable_and_frozen(self) -> None:
        """Spec: a privilege is a modeled artifact plus action pair."""
        from config import Privilege

        privilege = Privilege(artifact="epic", action="create")

        assert privilege in {privilege}
        with pytest.raises(FrozenInstanceError):
            privilege.action = "update"  # type: ignore[misc]


class TestAccessControlList:
    """Verify actor-to-privilege grants."""

    def test_lists_privileges_for_framework_actor(self, config_loader, framework_root) -> None:
        """Spec: ACL maps role privileges to declared framework agents."""
        write_yaml(
            framework_root / "conf" / "access-control-list.conf.yaml",
            """actors:
  - slug: planner
    roles: [author]
roles:
  - slug: author
    privileges:
      - artifact: epic
        action: create
""",
        )

        acl = config_loader.load_access_control_list(framework_root)

        assert acl.is_framework_agent("planner")
        assert not acl.is_framework_agent("ghost")
        assert {privilege.artifact for privilege in acl.list_privileges("planner")} == {"epic"}
        with pytest.raises(TypeError):
            acl.grants["other"] = frozenset()  # type: ignore[index]

    def test_rejects_actor_role_references_that_do_not_resolve(self, config_loader, framework_root) -> None:
        """Spec: fail-fast semantic validation rejects unknown ACL role refs."""
        write_yaml(
            framework_root / "conf" / "access-control-list.conf.yaml",
            """actors:
  - slug: planner
    roles: [missing-role]
roles:
  - slug: author
    privileges: []
""",
        )

        with pytest.raises(ConfigurationError, match="missing-role"):
            config_loader.load_access_control_list(framework_root)
