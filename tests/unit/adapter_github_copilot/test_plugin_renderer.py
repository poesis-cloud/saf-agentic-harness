"""The plugin renderer (adapter spec, Plugin mode — The harness plugin)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from cli_rig import ADAPTER_DIR, load_script

RENDER = load_script("github_copilot_render_plugin", "render_plugin.py")
REPO_ROOT = ADAPTER_DIR.parents[1]
AGENT_PLUGINS_FIELDS = {
    "$schema", "name", "version", "description", "author", "homepage", "repository",
    "license", "keywords", "extensions",
}


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    lock = tmp_path / "requirements.lock"
    lock.write_text("pyyaml==6.0.2 \\\n    --hash=sha256:00\n", encoding="utf-8")
    destination = tmp_path / "plugin"
    RENDER.render(destination, lock)
    return destination


def test_the_manifest_is_a_closed_agent_plugins_manifest(plugin: Path) -> None:
    manifest = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert set(manifest) <= AGENT_PLUGINS_FIELDS
    assert manifest["name"] == "poesis-saf-agentic-harness"
    assert manifest["version"] == (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert manifest["license"] == "Apache-2.0"
    assert manifest["homepage"] == "https://docs.poesis.cloud/saf/"
    assert manifest["author"] == {"name": "Poesis SAS", "email": "clement.cazaud@poesis.cloud"}
    assert manifest["keywords"] == [
        "harness", "organization", "governance", "regulation", "supervision", "coordination",
        "orchestration", "workflow", "agent", "skill", "instruction", "artifact", "template",
    ]
    assert manifest["description"].startswith("Deterministic governance - regulation")


def test_the_hooks_are_portable_shell_strings_in_the_copilot_extension_directory(
    plugin: Path,
) -> None:
    """Adapter spec: shell strings only (no `exec`), reaching the plugin through PLUGIN_ROOT."""
    rendered = (plugin / "com.github.copilot" / "hooks" / "hooks.json").read_text("utf-8")
    hooks = json.loads(rendered)["hooks"]
    registered = yaml.safe_load((ADAPTER_DIR / "hooks.yaml").read_text("utf-8"))["hooks"]

    assert set(hooks) == set(registered)
    assert str(REPO_ROOT) not in rendered and str(plugin) not in rendered
    for event, entries in hooks.items():
        for entry in entries:
            assert set(entry) == {"type", "bash", "powershell", "timeoutSec"}
            assert '"$PLUGIN_ROOT/bin/' in entry["bash"]
            assert '"$env:PLUGIN_ROOT\\bin\\' in entry["powershell"]
        if event != "sessionStart":
            assert [entry["timeoutSec"] for entry in entries] == [registered[event]["timeoutSec"]]


def test_session_start_registers_first_then_provisions(plugin: Path) -> None:
    hooks = json.loads(
        (plugin / "com.github.copilot" / "hooks" / "hooks.json").read_text("utf-8")
    )["hooks"]["sessionStart"]

    assert ["register.sh" in entry["bash"] for entry in hooks] == [True, False]
    assert hooks[1]["bash"].endswith("saf-hook.sh\" sessionStart")
    assert hooks[1]["timeoutSec"] == 300


def test_the_plugin_ships_the_runtime_subset_and_nothing_compiled(plugin: Path) -> None:
    for name in (
        "VERSION", "requirements.lock", "launcher.py", "README.md", "LICENSE", "NOTICE",
        "harness.py", "bin/saf-hook.sh", "bin/saf-hook.ps1", "bin/register.sh",
        "bin/register.ps1", "bin/launcher.sh", "bin/launcher.ps1",
        "adapters/binding_contract.py", "adapters/github-copilot/adapter.py",
        "adapters/github-copilot/tools.yaml", "adapters/github-copilot/models.yaml",
        "adapters/github-copilot/contracts/hook-stdout.schema.json",
        "src/application.py", "contracts/conf/workspace-marker.schema.json",
    ):
        assert (plugin / name).is_file(), name
    assert not list(plugin.rglob("__pycache__"))
    assert not (plugin / "adapters" / "github-copilot" / "install.py").exists()
    assert not (plugin / "adapters" / "github-copilot" / "plugin").exists()


def test_the_lock_is_shipped_as_given(plugin: Path, tmp_path: Path) -> None:
    assert (plugin / "requirements.lock").read_bytes() == (
        tmp_path / "requirements.lock"
    ).read_bytes()


def test_a_non_empty_destination_is_refused(tmp_path: Path) -> None:
    (tmp_path / "occupied").mkdir()
    (tmp_path / "occupied" / "file").write_text("x", encoding="utf-8")
    lock = tmp_path / "lock"
    lock.write_text("", encoding="utf-8")

    with pytest.raises(RENDER.RenderError, match="not empty"):
        RENDER.render(tmp_path / "occupied", lock)


def test_without_a_lock_or_uv_the_render_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(RENDER.shutil, "which", lambda name: None)

    assert RENDER.main(["--dest", str(tmp_path / "plugin")]) == 1
    assert not (tmp_path / "plugin" / "plugin.json").exists()
