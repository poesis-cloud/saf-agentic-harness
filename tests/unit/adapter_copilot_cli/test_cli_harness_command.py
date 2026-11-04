"""H4 — the ONE harness command an agent may run, recognized strictly and re-rendered.

The org bundle's orchestrators run `<python> <harness>/harness.py resolve-step
--workflow-slug <slug>` and `... resolve-step-model` with NO session ids, through
`powershell` (Windows) or `bash`. Any interpreter spelling is accepted; the harness path is
pinned; everything else that mentions `harness.py` is denied.
"""

from __future__ import annotations

import pytest

from cli_rig import CWD, HARNESS, HOST, PYTHON, TURN_SESSION, WORKSPACE, Rig, make_invocation, pre_tool_use

FORWARD = HARNESS.replace("\\", "/")


def _command(rig: Rig, command: str, tool: str = "powershell"):
    return rig.fire("preToolUse", pre_tool_use((tool, {"command": command, "description": "d"})))


@pytest.mark.parametrize(
    "command",
    [
        f"& {PYTHON} {HARNESS} resolve-step --workflow-slug pi-planning",
        f"& '{PYTHON}' '{HARNESS}' resolve-step --workflow-slug pi-planning",
        f'& "{PYTHON}" "{HARNESS}" resolve-step --workflow-slug "pi-planning"',
        f"python {HARNESS} resolve-step --workflow-slug pi-planning",
        f"python3 {HARNESS} resolve-step --workflow-slug pi-planning",
        f"python3.12 {HARNESS} resolve-step --workflow-slug pi-planning",
        f"py -3 {HARNESS} resolve-step --workflow-slug pi-planning",
        f"py -3.12 {FORWARD} resolve-step --workflow-slug pi-planning",
        f"python.exe {HARNESS.lower()} resolve-step --workflow-slug pi-planning",
        f"& C:\\Python312\\python.exe {HARNESS} resolve-step --workflow-slug pi-planning",
        f"  {PYTHON} {HARNESS}   resolve-step   --workflow-slug   pi-planning  ",
    ],
)
def test_every_interpreter_spelling_is_stamped_and_canonicalized(rig: Rig, command: str) -> None:
    rig.open_turn()
    decision = _command(rig, command)
    stamped = decision["modifiedArgs"]
    assert stamped["description"] == "d"
    assert stamped["command"] == (
        f"$env:FRAMEWORK_DIR='{CWD}'; $env:FRAMEWORK_WORKSPACE_DIR='{WORKSPACE}'; "
        f"& '{PYTHON}' '{HARNESS}' resolve-step --session-id {TURN_SESSION} "
        "--workflow-slug " + ('"pi-planning"' if '"pi-planning"' in command else "pi-planning")
    )


def test_resolve_step_model_is_stamped(rig: Rig) -> None:
    rig.open_turn()
    stamped = _command(rig, f"python {HARNESS} resolve-step-model")["modifiedArgs"]["command"]
    assert stamped.endswith(f"resolve-step-model --session-id {TURN_SESSION}")


def test_bash_gets_a_posix_rendering(tmp_path, binding) -> None:
    from cli_rig import FakeRunner
    from copilot_cli_adapter.core import Adapter
    from copilot_cli_adapter.payload import HookInput
    from copilot_cli_adapter.renderer import ContextRenderer
    from copilot_cli_adapter.tracker import SessionTracker

    (tmp_path / "i").mkdir()
    (tmp_path / "s").mkdir()
    invocation = make_invocation(
        python="/opt/h/.venv/bin/python",
        harness="/opt/h/harness.py",
        windows=False,
        environment={"FRAMEWORK_DIR": "/srv/o'rg"},
    )
    tracker = SessionTracker(tmp_path / "state")
    adapter = Adapter(
        binding, tracker, ContextRenderer(tmp_path / "i", tmp_path / "s"), FakeRunner(),
        "/srv/org/workspace", invocation, diagnostic=lambda _: None,
    )
    adapter.handle(HookInput("userPromptSubmitted", {"sessionId": "h", "prompt": "p"}, binding, "rte"))
    session = tracker.read("h").session_id
    decision = adapter.handle(
        HookInput(
            "preToolUse",
            {"sessionId": "h", "cwd": "/srv/org", "toolCalls": [
                {"name": "bash", "args": {"command": "python3 /opt/h/harness.py resolve-step-model"}}
            ]},
            binding,
            None,
        )
    )
    assert decision["modifiedArgs"]["command"] == (
        "FRAMEWORK_DIR='/srv/o'\"'\"'rg' '/opt/h/.venv/bin/python' '/opt/h/harness.py' "
        f"resolve-step-model --session-id {session}"
    )


@pytest.mark.parametrize(
    "command, expected",
    [
        (f"python {HARNESS} resolve-step --workflow-slug x; Remove-Item x", "one plain invocation"),
        (f"python {HARNESS} resolve-step --workflow-slug x | Out-File y", "one plain invocation"),
        (f"python {HARNESS} resolve-step --workflow-slug x > out.txt", "one plain invocation"),
        (f"python {HARNESS} resolve-step --workflow-slug $(whoami)", "one plain invocation"),
        (f"python {HARNESS} resolve-step --workflow-slug `whoami`", "one plain invocation"),
        (f'python {HARNESS} resolve-step --workflow-slug "$env:X"', "one plain invocation"),
        (f"python {HARNESS} resolve-step --workflow-slug x && echo", "one plain invocation"),
        (f"cd C:\\x; python {HARNESS} resolve-step", "one plain invocation"),
        (f"python {HARNESS} check-step-authorization --artifact-path a", "not agent-invocable"),
        (f"python {HARNESS} start-session --agent rte", "not agent-invocable"),
        (f"python {HARNESS} end-session", "not agent-invocable"),
        (f"python {HARNESS} resolve-step --session-id forged --workflow-slug x", "never accepted"),
        (f"python {HARNESS} resolve-step --parent-session-id p", "never accepted"),
        (f"node {HARNESS} resolve-step --workflow-slug x", "not a Python interpreter"),
        (f"C:\\evil\\pythonx.exe {HARNESS} resolve-step", "not a Python interpreter"),
        ("python C:\\elsewhere\\harness.py resolve-step --workflow-slug x", "not the installed harness"),
        ("python harness.py resolve-step --workflow-slug x", "not the installed harness"),
        (f"python -c 'import x' {HARNESS} resolve-step", "one plain invocation"),
    ],
)
def test_everything_else_mentioning_the_harness_is_denied(
    rig: Rig, command: str, expected: str
) -> None:
    rig.open_turn()
    decision = _command(rig, command)
    assert decision["permissionDecision"] == "deny"
    assert expected in decision["permissionDecisionReason"]


def test_a_harness_command_batched_with_another_call_is_denied(rig: Rig) -> None:
    rig.open_turn()
    decision = rig.fire(
        "preToolUse",
        pre_tool_use(
            ("powershell", {"command": f"python {HARNESS} resolve-step-model"}),
            ("view", {"path": "C:\\a"}),
        ),
    )
    assert decision["permissionDecision"] == "deny"
    assert "alone" in decision["permissionDecisionReason"]


def test_in_an_ungoverned_session_a_harness_command_is_denied_with_a_diagnosis(rig: Rig) -> None:
    decision = _command(rig, f"python {HARNESS} resolve-step --workflow-slug x")
    assert decision["permissionDecision"] == "deny"
    assert "trusted" in decision["permissionDecisionReason"]
    assert _command(rig, "git status --short") is None
    assert rig.runner.calls == []


def test_a_step_session_is_stamped_with_its_own_session(rig: Rig) -> None:
    from cli_rig import SUBAGENT

    rig.open_turn()
    rig.open_step()
    decision = rig.fire(
        "preToolUse",
        pre_tool_use(("powershell", {"command": f"python {HARNESS} resolve-step-model"}), host=SUBAGENT),
    )
    assert f"--session-id {SUBAGENT}" in decision["modifiedArgs"]["command"]
    assert HOST not in decision["modifiedArgs"]["command"]
