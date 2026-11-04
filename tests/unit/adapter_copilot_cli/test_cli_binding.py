"""tools.yaml / models.yaml: schema-valid, and classifying the app's real tool names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cli_rig import ADAPTER_DIR, REPO_ROOT
from contract_assertions import contract_validator
from copilot_cli_adapter.binding import (
    BindingError,
    TargetExtractionError,
    ToolBinding,
    load_tool_binding,
)


def _validate(conf_schema: str, document: object) -> None:
    path = REPO_ROOT / "contracts" / "conf" / "adapters" / conf_schema
    schema_id = json.loads(path.read_text(encoding="utf-8"))["$id"]
    records = contract_validator().validate_instance(schema_id, document)
    assert not records, records


def test_tools_yaml_honors_the_shared_tools_conf_schema() -> None:
    _validate("tools.conf.schema.json", yaml.safe_load((ADAPTER_DIR / "tools.yaml").read_text()))


def test_models_yaml_honors_the_shared_models_conf_schema() -> None:
    _validate("models.conf.schema.json", yaml.safe_load((ADAPTER_DIR / "models.yaml").read_text()))


def test_the_app_tools_classify(binding: ToolBinding) -> None:
    assert binding.resolve_write_action("create", {"path": "a"}) == "create"
    assert binding.resolve_write_action("edit", {"path": "a"}) == "update"
    assert binding.resolve_write_action("apply_patch", "x") == "update"
    for read_only in ("view", "grep", "glob", "skill", "ask_user", "task", "powershell"):
        assert binding.resolve_write_action(read_only, {}) is None
    assert binding.is_dispatch_tool("task")
    assert binding.is_mediated_command_tool("powershell") and binding.is_mediated_command_tool("bash")
    assert binding.is_guarded_shell_tool("powershell")


def test_a_write_without_a_path_cannot_be_extracted(binding: ToolBinding) -> None:
    with pytest.raises(TargetExtractionError):
        binding.extract_write_targets("create", {"file_text": "x"})
    with pytest.raises(TargetExtractionError):
        binding.extract_write_targets("edit", "not-an-object")
    with pytest.raises(TargetExtractionError):
        binding.extract_write_targets("apply_patch", {"input": "garbage"})


def test_str_replace_editor_view_is_not_a_write(binding: ToolBinding) -> None:
    assert binding.resolve_write_action("str_replace_editor", {"command": "view"}) is None
    assert binding.resolve_write_action("str_replace_editor", {"command": "create"}) == "create"
    assert binding.resolve_write_action("str_replace_editor", {"command": "str_replace"}) == "update"


def test_model_bindings_map_every_profile_slug(binding: ToolBinding) -> None:
    assert binding.model_bindings
    assert all(isinstance(value, str) and value for value in binding.model_bindings.values())


def test_a_broken_binding_is_refused(tmp_path: Path) -> None:
    (tmp_path / "tools.yaml").write_text("- not a mapping\n", encoding="utf-8")
    (tmp_path / "models.yaml").write_text("{}\n", encoding="utf-8")
    with pytest.raises(BindingError):
        load_tool_binding(tmp_path)
