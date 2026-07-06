"""Unit tests — FrameworkLayout: the framework's own path declarations (the framework is the
application embedding the harness; the workspace is a separate plane)."""

from __future__ import annotations

from pathlib import Path

from config import FrameworkLayout


class TestFrameworkLayout:
    def test_declared_paths_resolve_under_the_framework_root(self, tmp_path):
        layout = FrameworkLayout({"paths": {"skills": ".", "schemas": "registry/schemas"}})
        assert layout.skills_root(tmp_path) == tmp_path
        assert layout.schemas_registry(tmp_path) == (tmp_path / "registry" / "schemas").resolve()

    def test_undeclared_keys_fall_back_to_conventional_defaults(self, tmp_path):
        layout = FrameworkLayout({})
        assert layout.schemas_registry(tmp_path) == (tmp_path / "schemas").resolve()
        assert layout.agents_dir(tmp_path) == (tmp_path / "agents").resolve()
        assert layout.instructions_dir(tmp_path) == (tmp_path / "instructions").resolve()
        assert layout.prompts_dir(tmp_path) == (tmp_path / "prompts").resolve()
        assert layout.templates_dir(tmp_path) == (tmp_path / "templates").resolve()
        assert layout.skills_root(tmp_path) == tmp_path

    def test_dot_means_the_framework_root_itself(self, tmp_path):
        layout = FrameworkLayout({"paths": {"skills": "."}})
        assert layout.dir_of("skills", tmp_path) == tmp_path
