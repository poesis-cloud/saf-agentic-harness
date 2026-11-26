"""`claude-code` entry, plugin renderer and launcher: fail-closed exits, the rendered plugin,
and the launcher's governance context."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from claude_rig import ADAPTER_DIR, PLUGIN, SESSION, load_script

ENTRY = load_script("claude_code_entry", "adapter.py")
RENDER = load_script("claude_code_render_plugin", "render_plugin.py")
LAUNCHER = load_script("claude_code_launcher", "plugin/launcher.py")


def _run_entry(event: str, payload: dict, framework_dir: Path) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ENTRY.run_hook(
        ["hook", "--event", event, "--framework-dir", str(framework_dir)],
        io.BytesIO(json.dumps(payload).encode("utf-8")),
        stdout,
        stderr,
    )
    return code, stdout.getvalue(), stderr.getvalue()


@pytest.mark.parametrize(("event", "code"), [("PreToolUse", 2), ("Stop", 1), ("SubagentStop", 1), ("UserPromptSubmit", 1)])
def test_a_broken_bootstrap_blocks_only_where_the_runtime_can_veto(
    tmp_path: Path, event: str, code: int
) -> None:
    exit_code, stdout, stderr = _run_entry(event, {"session_id": SESSION}, tmp_path / "missing")
    assert (exit_code, stdout) == (code, "")
    assert "failing closed" in stderr


def test_a_malformed_command_line_never_traps_a_stopping_agent(capsys) -> None:
    assert ENTRY.main(["hook", "--event", "Stop", "--unknown"]) == 1
    assert ENTRY.main(["hook", "--event", "PreToolUse", "--unknown"]) == 2


def test_the_hook_deadline_stays_inside_the_registered_timeout() -> None:
    assert ENTRY.hook_deadline("PreToolUse", 0.0) == 90.0
    with pytest.raises(ValueError):
        ENTRY.hook_deadline("Notification", 0.0)


def test_the_rendered_hooks_reach_the_shim_through_the_plugin_root() -> None:
    hooks = RENDER.render_hooks()["hooks"]
    assert set(hooks) == {
        "SessionStart", "UserPromptSubmit", "SubagentStart", "PreToolUse", "PostToolUse",
        "PostToolUseFailure", "SubagentStop", "Stop", "SessionEnd",
    }
    for event, (group,) in hooks.items():
        (entry,) = group["hooks"]
        assert entry["command"] == f'sh "${{CLAUDE_PLUGIN_ROOT}}/bin/saf-hook.sh" {event}'
        assert ("matcher" in group) == (event in RENDER.TOOL_EVENTS)
    assert hooks["SessionStart"][0]["hooks"][0]["timeout"] == 300


def test_the_plugin_renders_the_runtime_with_the_shared_base(tmp_path: Path) -> None:
    lock = tmp_path / "requirements.lock"
    lock.write_text("pyyaml==6.0.2 --hash=sha256:00\n", encoding="utf-8")
    manifest = RENDER.render(tmp_path / "plugin", lock)
    plugin = manifest.parents[1]
    assert json.loads(manifest.read_text(encoding="utf-8"))["name"] == "poesis-saf-agentic-harness"
    for path in (
        "hooks/hooks.json", "launcher.py", "bin/saf-hook.sh", "harness.py", "VERSION",
        "requirements.lock", "adapters/binding_contract.py",
        "adapters/common/saf_host_adapter/core.py", "adapters/claude-code/adapter.py",
        "adapters/claude-code/claude_code_adapter/core.py",
        "adapters/claude-code/contracts/hook-stdout.schema.json",
    ):
        assert (plugin / path).is_file(), path
    assert not list(plugin.rglob("__pycache__"))
    with pytest.raises(RENDER.RenderError):
        RENDER.render(plugin, lock)


def _payload(event: str, cwd: Path, **fields: str) -> io.BytesIO:
    body = {"session_id": SESSION, "hook_event_name": event, "cwd": str(cwd), **fields}
    return io.BytesIO(json.dumps(body).encode("utf-8"))


@pytest.fixture
def launcher_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    registry = tmp_path / "registry"
    monkeypatch.setenv("SAF_AGENTIC_REGISTRY_DIR", str(registry))
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("SAF_HARNESS_PYTHON", "python3")
    return registry


def _launch(event: str, stdin: io.BytesIO) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    code = LAUNCHER.run([event], stdin, out, err)
    return code, out.getvalue()


def test_the_turn_framework_is_the_main_threads_agent_namespace() -> None:
    agent = {"agent_type": f"{PLUGIN}:release-train-engineer"}
    assert LAUNCHER.turn_framework("UserPromptSubmit", agent) == PLUGIN
    assert LAUNCHER.turn_framework("PreToolUse", agent) is None
    assert LAUNCHER.turn_framework("UserPromptSubmit", {**agent, "agent_id": "a1"}) is None
    assert LAUNCHER.turn_framework("UserPromptSubmit", {"agent_type": "plain"}) is None


def test_an_ungoverned_folder_passes_without_running_the_adapter(launcher_env: Path, tmp_path: Path) -> None:
    assert _launch("PreToolUse", _payload("PreToolUse", tmp_path, tool_name="Bash")) == (0, "")
    assert _launch("UserPromptSubmit", _payload("UserPromptSubmit", tmp_path, agent_type="other:x")) == (0, "")


def test_an_orchestrator_in_an_unmarked_folder_is_told_and_its_intent_recorded(
    launcher_env: Path, tmp_path: Path
) -> None:
    entry = LAUNCHER.framework_entry(launcher_env, PLUGIN)
    LAUNCHER.write_entry(entry, str(tmp_path))
    code, out = _launch(
        "UserPromptSubmit",
        _payload("UserPromptSubmit", tmp_path, agent_type=f"{PLUGIN}:release-train-engineer"),
    )
    assert code == 0
    assert json.loads(out)["hookSpecificOutput"] == {
        "hookEventName": "UserPromptSubmit", "additionalContext": LAUNCHER.NOT_A_WORKSPACE,
    }
    intent = launcher_env / "intents" / SESSION
    assert intent.is_file()
    assert _launch("SessionEnd", _payload("SessionEnd", tmp_path)) == (0, "")
    assert not intent.exists()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_a_workspace_whose_framework_is_not_registered_denies_tool_calls(
    launcher_env: Path, tmp_path: Path
) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".saf").mkdir(parents=True)
    (workspace / ".saf" / "workspace.yaml").write_text(f"framework: {PLUGIN}\nroot: portfolio\n", encoding="utf-8")
    _git(workspace, "init", "-q")
    _git(workspace, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".saf")
    _git(workspace, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    code, out = _launch("PreToolUse", _payload("PreToolUse", workspace, tool_name="Bash"))
    decision = json.loads(out)["hookSpecificOutput"]
    assert code == 0 and decision["permissionDecision"] == "deny"
    assert LAUNCHER.NOT_REGISTERED in decision["permissionDecisionReason"]
    assert _launch("SessionStart", _payload("SessionStart", workspace)) == (0, "")


def test_an_uncommitted_marker_denies_tool_calls_with_the_remedy(launcher_env: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".saf").mkdir(parents=True)
    (workspace / ".saf" / "workspace.yaml").write_text(f"framework: {PLUGIN}\nroot: portfolio\n", encoding="utf-8")
    _git(workspace, "init", "-q")
    code, out = _launch("PreToolUse", _payload("PreToolUse", workspace, tool_name="Bash"))
    assert "is not committed" in json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    code, out = _launch("SessionStart", _payload("SessionStart", workspace))
    assert "is not committed" in json.loads(out)["systemMessage"]


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX shim")
def test_the_shim_blocks_a_governed_tool_call_when_no_python_is_found(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".saf").mkdir(parents=True)
    (workspace / ".saf" / "workspace.yaml").write_text("framework: x\nroot: y\n", encoding="utf-8")
    tools = tmp_path / "bin"
    tools.mkdir()
    import shutil

    for tool in ("cat", "tr", "grep", "head", "sed", "dirname", "mkdir", "printf"):
        found = shutil.which(tool)
        if found:
            (tools / tool).symlink_to(found)
    environment = {"PATH": str(tools), "HOME": str(tmp_path),
                   "SAF_AGENTIC_REGISTRY_DIR": str(tmp_path / "registry")}
    shim = ADAPTER_DIR / "plugin" / "bin" / "saf-hook.sh"
    payload = json.dumps({"session_id": SESSION, "cwd": str(workspace), "tool_name": "Bash"})
    blocked = subprocess.run(["/bin/sh", str(shim), "PreToolUse"], input=payload, text=True,
                             capture_output=True, env=environment, cwd=tmp_path)
    assert blocked.returncode == 2 and "Python >= 3.10 was not found" in blocked.stderr
    elsewhere = json.dumps({"session_id": SESSION, "cwd": str(tmp_path), "tool_name": "Bash"})
    passed = subprocess.run(["/bin/sh", str(shim), "PreToolUse"], input=elsewhere, text=True,
                            capture_output=True, env=environment, cwd=tmp_path)
    assert passed.returncode == 0


@pytest.mark.parametrize(
    ("event", "code", "stdout", "expected_code", "denied"),
    [
        ("PreToolUse", 1, b"", 0, True),
        ("PreToolUse", -9, b"", 0, True),
        ("PreToolUse", 0, b"Traceback: not json", 0, True),
        ("PreToolUse", 0, b"[1, 2]", 0, True),
        ("Stop", 1, b"", 1, False),
        ("UserPromptSubmit", -9, b"", 1, False),
        ("SubagentStop", 0, b"not json", 1, False),
    ],
)
def test_a_failed_adapter_never_lets_a_governed_call_run(
    event: str, code: int, stdout: bytes, expected_code: int, denied: bool
) -> None:
    out, err = io.StringIO(), io.StringIO()
    assert LAUNCHER.relay(event, code, stdout, b"Traceback\nModuleNotFoundError: yaml\n", out, err) == expected_code
    if denied:
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "ModuleNotFoundError: yaml" in decision["permissionDecisionReason"]
    else:
        assert out.getvalue() == ""
    assert "Traceback" in err.getvalue()


def test_a_failed_adapter_warns_at_session_start() -> None:
    out = io.StringIO()
    assert LAUNCHER.relay("SessionStart", 1, b"", b"boom\n", out, io.StringIO()) == 0
    assert "boom" in json.loads(out.getvalue())["systemMessage"]


@pytest.mark.parametrize("stdout", [b"", b"  \n", b'{"hookSpecificOutput": {"hookEventName": "PreToolUse"}}\n'])
def test_a_valid_adapter_answer_is_relayed_unchanged(stdout: bytes) -> None:
    out = io.StringIO()
    assert LAUNCHER.relay("PreToolUse", 0, stdout, b"", out, io.StringIO()) == 0
    assert out.getvalue() == stdout.decode("utf-8")


@pytest.mark.parametrize(("event", "code"), [("PreToolUse", 2), ("Stop", 1), ("SessionStart", 1)])
def test_an_adapter_that_cannot_load_fails_closed_only_where_the_runtime_can_veto(
    tmp_path: Path, event: str, code: int
) -> None:
    """A broken runtime (here: `yaml` cannot be imported) exits before any hook handling."""
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "yaml.py").write_text("raise ImportError('yaml is broken')\n", encoding="utf-8")
    import os
    import sys

    done = subprocess.run(
        [sys.executable, str(ADAPTER_DIR / "adapter.py"), "hook", "--event", event],
        input=b"{}", capture_output=True, env={**os.environ, "PYTHONPATH": str(shadow)},
    )
    assert done.returncode == code
    assert b"could not load" in done.stderr and done.stdout == b""
