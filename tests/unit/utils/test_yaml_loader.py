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


class TestJsonDataParsing:
    """Artifact YAML is read in the JSON data model its JSON Schemas validate."""

    def test_keeps_implicit_and_tagged_timestamps_as_strings(self) -> None:
        data = YamlLoader().parse_json_data(
            "day: 2026-11-04\nat: 2026-11-04T10:00:00Z\ntagged: !!timestamp 2026-11-04\n"
            "n: 3\nok: true\nnothing: null\n"
        )

        assert dict(data) == {
            "day": "2026-11-04", "at": "2026-11-04T10:00:00Z", "tagged": "2026-11-04",
            "n": 3, "ok": True, "nothing": None,
        }

    def test_leaves_configuration_loading_unchanged(self, tmp_path) -> None:
        import datetime

        yaml_file = tmp_path / "sample.yaml"
        yaml_file.write_text("day: 2026-11-04\n", encoding="utf-8")

        assert YamlLoader().load_yaml(yaml_file)["day"] == datetime.date(2026, 11, 4)

    def test_rejects_unsafe_yaml_tags(self) -> None:
        with pytest.raises(yaml.constructor.ConstructorError):
            YamlLoader().parse_json_data("!!python/object/apply:os.system ['echo unsafe']\n")

    @pytest.mark.parametrize(
        "text",
        [
            "tags: !!set {a: null}\n",
            "blob: !!binary aGVsbG8=\n",
            "items:\n  - ok\n  - !!set {b: null}\n",
            "ratio: .nan\n",
            "limit: .inf\n",
            "? !!binary aGVsbG8=\n: x\n",
        ],
        ids=["set", "binary", "nested-set", "nan", "inf", "binary-key"],
    )
    def test_rejects_yaml_only_values_as_a_parse_failure(self, text: str) -> None:
        """Artifact data is the JSON data model its schemas validate: a YAML-only value is
        refused at load (a ValueError, the artifact store's parse failure)."""
        from utils.yaml_loader import NonJsonDataError

        with pytest.raises(NonJsonDataError) as raised:
            YamlLoader().parse_json_data(text)
        assert isinstance(raised.value, ValueError)

    def test_markdown_frontmatter_rejects_yaml_only_values(self) -> None:
        from utils import MarkdownLoader

        with pytest.raises(ValueError):
            MarkdownLoader().parse_markdown("---\ntags: !!set {a: null}\n---\n\n# Body\n")
