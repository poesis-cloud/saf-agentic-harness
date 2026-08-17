"""Unit tests for YAML loading mechanics."""

from __future__ import annotations

import pytest
import yaml

from utils import YamlLoader


class TestYamlLoader:
    """Verify safe YAML loading."""

    def test_loads_yaml_file_with_safe_loader(self, tmp_path) -> None:
        """Spec: YamlLoader uses PyYAML safe_load."""
        yaml_file = tmp_path / "sample.yaml"
        yaml_file.write_text("name: safe\nitems:\n  - one\n  - two\n", encoding="utf-8")

        data = YamlLoader().load_yaml(yaml_file)

        assert data["name"] == "safe"
        assert data["items"] == ("one", "two")
        with pytest.raises(TypeError):
            data["name"] = "changed"  # type: ignore[index]

    def test_rejects_unsafe_yaml_tags(self, tmp_path) -> None:
        """Spec: PyYAML safe_load rejects arbitrary Python object construction."""
        yaml_file = tmp_path / "unsafe.yaml"
        yaml_file.write_text("!!python/object/apply:os.system ['echo unsafe']\n", encoding="utf-8")

        with pytest.raises(yaml.constructor.ConstructorError):
            YamlLoader().load_yaml(yaml_file)
