"""Unit tests for FrameworkLayout."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest


class TestFrameworkLayout:
    """Verify FrameworkLayout is a frozen typed view of framework paths."""

    def test_is_frozen_and_exposes_resolved_directories(self, config_loader, framework_root) -> None:
        """Spec: configuration dataclasses are immutable typed views."""
        layout = config_loader.load_framework_layout(framework_root)

        assert layout.framework_dir.is_absolute()
        assert layout.artifacts_dir == layout.schemas_dir
        assert layout.workflows_dir.name == "workflows"
        with pytest.raises(FrozenInstanceError):
            layout.skills_dir = layout.agents_dir  # type: ignore[misc]
