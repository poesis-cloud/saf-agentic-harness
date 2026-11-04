"""install.py — one global hook file + the orchestrators' scoped H0 block; refuse, never half-write."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from cli_rig import ADAPTER_DIR, load_script

INSTALL = load_script("copilot_cli_adapter_install", "install.py")
LAYOUT = ("agents", "artifacts", "instructions", "skills", "templates", "workflows", "workspace")
ORCHESTRATORS = ("release-train-engineer", "scrum-master")
AGENT = "---\nname: {slug}\ndescription: d\ntools: [view, task, skill, ask_user, powershell]\n---\n\nBody of {slug}.\n"


@pytest.fixture
def framework(tmp_path: Path) -> Path:
    root = tmp_path / "org checkout"
    for name in LAYOUT:
        (root / name).mkdir(parents=True)
    (root / ".env").write_text(
        "\n".join(f"FRAMEWORK_{name.upper()}_DIR={name}" for name in LAYOUT) + "\n",
        encoding="utf-8",
    )
    for index, slug in enumerate(ORCHESTRATORS):
        (root / "workflows" / f"w{index}.workflow.conf.yaml").write_text(
            f"orchestrator: {slug}\n", encoding="utf-8"
        )
    (root / "workflows" / "plain.workflow.conf.yaml").write_text("steps: []\n", encoding="utf-8")
    return root


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "agents").mkdir(parents=True)
    for slug in (*ORCHESTRATORS, "developer"):
        (root / "agents" / f"{slug}.agent.md").write_text(AGENT.format(slug=slug), encoding="utf-8")
    (root / "plugin.json").write_text(
        json.dumps({"name": "org", "agents": "agents/", "skills": "skills/"}), encoding="utf-8"
    )
    return root


def _install(*argv: str, environment=None) -> int:
    return INSTALL.main(list(argv), environment=environment or {})


def test_a_bundle_install_writes_plugin_hooks_and_scoped_blocks(framework, bundle, tmp_path) -> None:
    workspace = tmp_path / "portfolio"
    workspace.mkdir()
    assert _install(
        "--framework-dir", str(framework), "--bundle-dir", str(bundle), "--workspace-dir", str(workspace)
    ) == 0
    rendered = json.loads((bundle / "hooks.json").read_text(encoding="utf-8"))
    assert rendered["version"] == 1
    registered = yaml.safe_load((ADAPTER_DIR / "hooks.yaml").read_text(encoding="utf-8"))["hooks"]
    assert set(rendered["hooks"]) == set(registered) and "sessionStart" in rendered["hooks"]
    for event, (entry,) in rendered["hooks"].items():
        assert entry["type"] == "command" and entry["exec"] == sys.executable
        args = entry["args"]
        assert args[0] == str(ADAPTER_DIR / "adapter.py") and args[1:4] == ["hook", "--event", event]
        assert args[args.index("--framework-dir") + 1] == str(framework)
        assert args[args.index("--workspace-dir") + 1] == str(workspace)
        assert "--agent" not in args
        assert entry["cwd"] == str(framework)
        assert entry["timeoutSec"] == registered[event]["timeoutSec"]
        env = entry["env"]
        assert env["FRAMEWORK_DIR"] == str(framework)
        assert env["FRAMEWORK_WORKSPACE_DIR"] == str(workspace)
        assert {f"FRAMEWORK_{name.upper()}_DIR" for name in LAYOUT} <= set(env)
        assert env["PYTHONUTF8"] == "1"
    for slug in ORCHESTRATORS:
        text = (bundle / "agents" / f"{slug}.agent.md").read_text(encoding="utf-8")
        front = yaml.safe_load(text.split("---\n")[1])
        assert front["name"] == slug and text.endswith(f"Body of {slug}.\n")
        hooks = front["hooks"]
        assert set(hooks) == {"userPromptSubmitted", "userPromptTransformed"}
        (entry,) = hooks["userPromptSubmitted"]
        assert f"--agent {slug}" in entry["command"] and "--workspace-dir" in entry["command"]
        assert entry["windows"].startswith("& '") and f"'--agent' '{slug}'" in entry["windows"]
    assert "hooks" not in (bundle / "agents" / "developer.agent.md").read_text(encoding="utf-8")


def test_reinstalling_replaces_the_managed_block(framework, bundle) -> None:
    argv = ("--framework-dir", str(framework), "--bundle-dir", str(bundle))
    assert _install(*argv) == 0
    first = (bundle / "agents" / "scrum-master.agent.md").read_text(encoding="utf-8")
    assert _install(*argv) == 0
    assert (bundle / "agents" / "scrum-master.agent.md").read_text(encoding="utf-8") == first
    assert first.count(INSTALL.BLOCK_OPEN) == 1


def test_shell_style_renders_powershell_and_bash(framework, bundle) -> None:
    assert _install(
        "--framework-dir", str(framework), "--bundle-dir", str(bundle), "--hook-style", "shell"
    ) == 0
    (entry,) = json.loads((bundle / "hooks.json").read_text())["hooks"]["preToolUse"]
    assert "exec" not in entry
    assert entry["powershell"].startswith(f"& '{sys.executable}' ")
    assert f"'{framework}'" in entry["powershell"] and "'org checkout'" not in entry["bash"].split()[0]


def test_a_repo_install_writes_the_ignored_hook_file(framework, tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _install("--framework-dir", str(framework), "--target-repo", str(repo), "--skip-agents") == 0
    assert (repo / ".github" / "hooks" / INSTALL.HOOKS_FILENAME).is_file()


def test_a_user_install_honors_copilot_home(framework, tmp_path) -> None:
    home = tmp_path / "copilot-home"
    assert _install(
        "--framework-dir", str(framework), "--hooks-target", "user", "--skip-agents",
        environment={"COPILOT_HOME": str(home)},
    ) == 0
    assert (home / "hooks" / INSTALL.HOOKS_FILENAME).is_file()


def test_plugin_json_hooks_path_is_honored_and_inline_hooks_refused(framework, bundle) -> None:
    manifest = bundle / "plugin.json"
    manifest.write_text(json.dumps({"name": "org", "hooks": "hooks/hooks.json"}), encoding="utf-8")
    assert _install("--framework-dir", str(framework), "--bundle-dir", str(bundle), "--skip-agents") == 0
    assert (bundle / "hooks" / "hooks.json").is_file()
    manifest.write_text(json.dumps({"name": "org", "hooks": {"preToolUse": []}}), encoding="utf-8")
    assert _install("--framework-dir", str(framework), "--bundle-dir", str(bundle), "--skip-agents") == 1


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda fw, b: (b / "agents" / "scrum-master.agent.md").unlink(), "no agent file"),
        (
            lambda fw, b: (b / "agents" / "scrum-master.agent.md").write_text(
                "---\nname: x\nhooks:\n  agentStop: []\n---\n", encoding="utf-8"
            ),
            "unmanaged",
        ),
        (lambda fw, b: (fw / ".env").write_text("FRAMEWORK_AGENTS_DIR=agents\n"), "declares no"),
        (lambda fw, b: (b / "agents" / "scrum-master.agent.md").write_text("no frontmatter\n"), "frontmatter"),
    ],
)
def test_a_refused_install_writes_nothing(framework, bundle, capsys, mutate, message) -> None:
    mutate(framework, bundle)
    before = {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()}
    assert _install("--framework-dir", str(framework), "--bundle-dir", str(bundle)) == 1
    assert message in capsys.readouterr().err
    assert {p: p.read_bytes() for p in bundle.rglob("*") if p.is_file()} == before


def test_a_missing_workspace_dir_is_refused(framework, bundle, tmp_path) -> None:
    assert _install(
        "--framework-dir", str(framework), "--bundle-dir", str(bundle),
        "--workspace-dir", str(tmp_path / "absent"),
    ) == 1
    assert not (bundle / "hooks.json").exists()


def test_dry_run_writes_nothing(framework, bundle, capsys) -> None:
    assert _install("--framework-dir", str(framework), "--bundle-dir", str(bundle), "--dry-run") == 0
    assert not (bundle / "hooks.json").exists()
    assert "would write" in capsys.readouterr().out


def test_windows_paths_are_quoted_for_powershell() -> None:
    args = INSTALL.adapter_args("preToolUse", r"C:\Users\o'neil\org", agent="rte")
    command = INSTALL.powershell_command(r"C:\Program Files\h\.venv\Scripts\python.exe", args)
    assert command.startswith(r"& 'C:\Program Files\h\.venv\Scripts\python.exe' ")
    assert r"'C:\Users\o''neil\org'" in command
