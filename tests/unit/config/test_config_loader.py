"""Unit tests for ConfigLoader fail-fast source loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from errors import ConfigurationError
from tests.unit.config.conftest import write_standard_configs, write_yaml


class TestConfigLoader:
    """Verify ConfigLoader parses, validates, and builds typed views atomically."""

    def test_loads_layout_with_process_environment_precedence(self, config_loader, framework_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec: process environment overrides .env and FRAMEWORK_DIR defaults to .env parent."""
        (framework_root / "agents-ci").mkdir()
        monkeypatch.setenv("FRAMEWORK_AGENTS_DIR", "agents-ci")

        layout = config_loader.load_framework_layout(framework_root)

        assert layout.framework_dir == framework_root.resolve()
        assert layout.agents_dir == (framework_root / "agents-ci").resolve()
        assert layout.skills_dir == (framework_root / "skills").resolve()
        assert layout.schemas_dir == (framework_root / "artifacts").resolve()
        assert layout.workspace_dir == framework_root.parent / "workspace"

    def test_rejects_missing_required_environment_variable(self, config_loader, framework_root: Path) -> None:
        """Spec: every required layout variable is present before use."""
        (framework_root / ".env").write_text("FRAMEWORK_AGENTS_DIR=agents\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="FRAMEWORK_ARTIFACTS_DIR"):
            config_loader.load_framework_layout(framework_root)

    def test_rejects_nonexistent_environment_path(self, config_loader, framework_root: Path) -> None:
        """Spec: every declared layout path must exist."""
        (framework_root / "skills").rmdir()

        with pytest.raises(ConfigurationError, match="FRAMEWORK_SKILLS_DIR"):
            config_loader.load_framework_layout(framework_root)

    def test_rejects_schema_invalid_yaml_without_returning_partial_view(self, config_loader, framework_root: Path) -> None:
        """Spec: source parse and contract validation are one fail-fast act."""
        write_yaml(framework_root / "conf" / "model-profiles.conf.yaml", "modelProfiles: []\nextra: no\n")

        with pytest.raises(ConfigurationError, match="schema validation failed"):
            config_loader.load_model_profiles(framework_root)

    def test_loads_all_configuration_sources_against_real_contracts(self, config_loader, framework_root: Path) -> None:
        """Spec: each load_* method returns an immutable typed configuration view."""
        write_standard_configs(framework_root)

        assert config_loader.load_access_control_list(framework_root).is_framework_agent("planner")
        assert "fast-coder" in config_loader.load_model_profiles(framework_root).profiles
        assert config_loader.load_workspace_layout(framework_root).resolve_resource(Path("portfolio/epics/a.md"), "epic") == "epic"
        assert config_loader.load_workflow_catalog(framework_root).find_workflow("planning").slug == "planning"
