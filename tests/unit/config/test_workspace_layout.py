"""Unit tests for workspace layout configuration views."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from config import ArtifactNode, FolderNode, WorkspaceLayout
from errors import ConfigurationError
from tests.unit.config.conftest import workspace_yaml, write_yaml


class TestArtifactNode:
    """Verify artifact node typed views."""

    def test_is_frozen_leaf_binding(self, config_loader, framework_root) -> None:
        """Spec: artifact nodes bind a path segment to artifact and template slugs."""
        write_yaml(framework_root / "conf" / "workspace.conf.yaml", workspace_yaml())

        folder = config_loader.load_workspace_layout(framework_root).nodes[0]
        node = folder.children[0].children[0]

        assert node.slug == "<item-slug>.epic.md"
        assert node.artifact == "epic"
        with pytest.raises(FrozenInstanceError):
            node.artifact = "feature"  # type: ignore[misc]


class TestFolderNode:
    """Verify folder node typed views."""

    def test_is_frozen_container_with_tuple_children(self, config_loader, framework_root) -> None:
        """Spec: folder nodes are containers, not artifact bindings."""
        write_yaml(framework_root / "conf" / "workspace.conf.yaml", workspace_yaml())

        folder = config_loader.load_workspace_layout(framework_root).nodes[0]

        assert folder.cardinality is None
        assert isinstance(folder.children, tuple)
        with pytest.raises(FrozenInstanceError):
            folder.slug = "changed"  # type: ignore[misc]


class TestWorkspaceLayout:
    """Verify resource resolution and logs guard behavior."""

    def test_resolve_resource_binds_placeholders_and_ignores_a_property_fragment(self, config_loader, framework_root) -> None:
        """Spec (function 8, invariant 2): the resource is resolved from the write path;
        invariant 3: authorization is whole-resource, so a `#property` suffix is ignored."""
        write_yaml(framework_root / "conf" / "workspace.conf.yaml", workspace_yaml())
        layout = config_loader.load_workspace_layout(framework_root)

        assert layout.resolve_resource(Path("portfolio/epics/payments.feature.md")) == "feature"
        assert layout.resolve_resource(Path("portfolio/epics/payments.epic.md#title")) == "epic"
        with pytest.raises(ConfigurationError, match="unresolvable"):
            layout.resolve_resource(Path("portfolio/epics/payments.md"))

    def test_resolve_resource_refuses_a_layout_the_loader_would_have_rejected(self) -> None:
        """Spec (function 8, invariant 2): resolution answers ONE schema identity — the
        ambiguous case is unreachable through `ConfigLoader`, so this raise is the value
        object defending itself against direct construction."""
        layout = WorkspaceLayout(
            nodes=(
                FolderNode(
                    slug="portfolio",
                    description="Portfolio folder",
                    children=(
                        ArtifactNode(
                            slug="<item-slug>.md",
                            description="Epic",
                            cardinality="0..*",
                            artifact="epic",
                            template="epic",
                        ),
                        ArtifactNode(
                            slug="<item-slug>.md",
                            description="Feature",
                            cardinality="0..*",
                            artifact="feature",
                            template="feature",
                        ),
                    ),
                ),
            )
        )

        with pytest.raises(ConfigurationError) as failure:
            layout.resolve_resource(Path("portfolio/payments.md"))

        assert failure.value.code == "ambiguous-resource"

    def test_repeated_placeholders_must_bind_consistently(self, config_loader, framework_root) -> None:
        """Spec: placeholder names may recur across descendants and share one binding."""
        write_yaml(
            framework_root / "conf" / "workspace.conf.yaml",
            """nodes:
  - slug: <epic-slug>
    description: Epic folder
    children:
      - slug: <epic-slug>.md
        description: Epic file
        cardinality: "1"
        artifact: epic
        template: epic
""",
        )
        layout = config_loader.load_workspace_layout(framework_root)

        assert layout.resolve_resource(Path("payments/payments.md")) == "epic"
        with pytest.raises(ConfigurationError, match="unresolvable"):
            layout.resolve_resource(Path("payments/other.md"))

    def test_is_logs_path_identifies_workspace_logs_plane(self, config_loader, framework_root) -> None:
        """Spec: writes targeting workspace logs are denied by layout guard."""
        write_yaml(framework_root / "conf" / "workspace.conf.yaml", workspace_yaml())
        layout = config_loader.load_workspace_layout(framework_root)

        assert layout.is_logs_path(Path("logs/session-1.log.jsonl"))
        assert layout.is_logs_path(Path("logs"))
        assert not layout.is_logs_path(Path("portfolio/epics/payments.md"))
        assert not layout.is_logs_path(Path("catalog/logs/readme.md"))
