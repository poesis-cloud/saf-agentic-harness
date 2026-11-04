"""H4 — the ONE harness command an agent may run, recognized strictly and re-rendered.

The org bundle's orchestrators run `<python> <harness>/harness.py resolve-step
--workflow-slug <slug>` and `... resolve-step-model` with NO session ids, through
`powershell` (Windows) or `bash`. Any interpreter spelling is accepted; the harness path is
pinned; everything else that mentions `harness.py` is denied.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli_rig import (
    CWD,
    HARNESS,
    HOST,
    PYTHON,
    TURN_SESSION,
    WORKSPACE,
    Rig,
    make_invocation,
    post_tool_use,
    pre_tool_use,
)
from copilot_cli_adapter.invocation import needs_quoting, shell_safe

FORWARD = HARNESS.replace("\\", "/")
LAYOUT_TEXT = f"FRAMEWORK_DIR={CWD}\nFRAMEWORK_WORKSPACE_DIR={WORKSPACE}\n"


def _command(rig: Rig, command: str, tool: str = "powershell"):
    return rig.fire("preToolUse", pre_tool_use((tool, {"command": command, "description": "d"})))


def _split_rewrite(command: str, prefix: str) -> tuple[Path, str]:
    """`<prefix> --env-file <layout> <rest>` -> (layout file, rest)."""
    assert command.startswith(f"{prefix} --env-file "), command
    layout, _, rest = command[len(prefix) + len(" --env-file ") :].partition(" ")
    return Path(layout.strip("'")), rest


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
    layout, rest = _split_rewrite(stamped["command"], f"& {PYTHON} {HARNESS}")
    assert rest == (
        f"resolve-step --session-id {TURN_SESSION} "
        "--workflow-slug " + ('"pi-planning"' if '"pi-planning"' in command else "pi-planning")
    )
    assert layout.read_text(encoding="utf-8") == LAYOUT_TEXT


def test_the_rewrite_stays_approvable_by_an_interpreter_rule(rig: Rig) -> None:
    """Live trial (app 1.0.102): the host's permission layer checks the REWRITTEN line; an
    `$env:X=...;` / `X=... cmd` prefix or a quoted interpreter after `&` can match no saved
    rule, so every unattended harness command was denied. The rewrite is ONE plain command
    led by the bare interpreter path."""
    rig.open_turn()
    command = _command(rig, f"python {HARNESS} resolve-step-model")["modifiedArgs"]["command"]
    assert command.startswith(f"& {PYTHON} ")
    assert "$env:" not in command and "'" not in command and '"' not in command
    assert ";" not in command and "FRAMEWORK_" not in command


def test_the_layout_file_is_content_addressed(tmp_path: Path) -> None:
    first = make_invocation(layout_dir=str(tmp_path))
    again = make_invocation(layout_dir=str(tmp_path))
    other = make_invocation(layout_dir=str(tmp_path), environment={"FRAMEWORK_DIR": "/elsewhere"})
    assert first.publish_layout() == again.publish_layout() != other.publish_layout()
    assert Path(other.publish_layout()).read_text(encoding="utf-8") == "FRAMEWORK_DIR=/elsewhere\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        {Path(first.layout_file()).name, Path(other.layout_file()).name}
    )


def test_a_layout_file_that_cannot_be_published_denies(rig: Rig, tmp_path: Path, binding) -> None:
    from copilot_cli_adapter.core import Adapter
    from copilot_cli_adapter.renderer import ContextRenderer
    from cli_rig import FakeRunner

    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    (tmp_path / "i").mkdir()
    (tmp_path / "s").mkdir()
    diagnostics: list[str] = []
    adapter = Adapter(
        binding, rig.tracker, ContextRenderer(tmp_path / "i", tmp_path / "s"), FakeRunner(),
        WORKSPACE, make_invocation(layout_dir=str(blocker / "layouts")), diagnostic=diagnostics.append,
    )
    rig.open_turn()
    from copilot_cli_adapter.payload import HookInput

    decision = adapter.handle(
        HookInput("preToolUse", pre_tool_use(("powershell", {"command": f"python {HARNESS} resolve-step-model"})), binding, None)
    )
    assert decision["permissionDecision"] == "deny"
    assert "layout file cannot be published" in decision["permissionDecisionReason"]
    assert diagnostics


def test_a_layout_value_that_is_not_one_line_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        make_invocation(layout_dir=str(tmp_path), environment={"FRAMEWORK_DIR": "a\nB=c"}).publish_layout()


def test_a_path_needing_quotes_falls_back_to_a_quoted_word(rig: Rig, tmp_path: Path, binding) -> None:
    """No unquoted spelling exists (no 8.3 name): the line still runs, approved interactively."""
    invocation = make_invocation(python=r"C:\no such dir\python.exe", layout_dir=str(tmp_path))
    command = invocation.canonical("resolve-step-model", ("--session-id", "s"), "", powershell=True)
    assert command.startswith(r"& 'C:\no such dir\python.exe' ")
    posix = make_invocation(python="/no such/python", layout_dir=str(tmp_path), windows=False)
    assert posix.canonical("resolve-step-model", (), "", powershell=False).startswith("'/no such/python' ")
    backslashed = make_invocation(layout_dir=str(tmp_path)).canonical(
        "resolve-step-model", (), "", powershell=False
    )
    assert backslashed.startswith(f"'{PYTHON}' '{HARNESS}' ")


@pytest.mark.parametrize(
    "path, quoted",
    [
        (r"C:\Users\cleme\.venv\Scripts\python.exe", False),
        (r"C:\Users\RUNNER~1\AppData\Local\Temp\x", False),
        ("/opt/h/.venv/bin/python3.12", False),
        (r"C:\Program Files\Python\python.exe", True),
        ("~/venv/bin/python", True),
        ("-python", True),
        ("/srv/o'rg/python", True),
        ("/srv/$HOME/python", True),
        ("/srv/a&b/python", True),
        ("/srv/a;b/python", True),
        ("/srv/a(b)/python", True),
        ("", True),
    ],
)
def test_only_plain_words_go_unquoted(path: str, quoted: bool) -> None:
    assert needs_quoting(path) is quoted


def test_shell_safe_never_changes_a_plain_path() -> None:
    assert shell_safe(PYTHON) == PYTHON
    assert shell_safe("/opt/h/python") == "/opt/h/python"


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
        layout_dir=str(tmp_path / "layouts"),
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
    layout, rest = _split_rewrite(
        decision["modifiedArgs"]["command"], "/opt/h/.venv/bin/python /opt/h/harness.py"
    )
    assert rest == f"resolve-step-model --session-id {session}"
    assert layout.read_text(encoding="utf-8") == "FRAMEWORK_DIR=/srv/o'rg\n"
    # No `VAR=value cmd` prefix: the bare interpreter path leads, as an approval rule needs.
    assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", decision["modifiedArgs"]["command"]) is None


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
        (f"python {HARNESS} --env-file C:\\x.env resolve-step", "'--env-file' is never accepted"),
        (f"python {HARNESS} resolve-step --env-file C:\\x.env", "'--env-file' is never accepted"),
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


def test_a_failed_harness_command_is_explained_as_the_adapters_rewrite(rig: Rig) -> None:
    """Live trial: the model read the host's denial of the REWRITTEN line, took the added
    `--session-id` for its own mistake and retried five times. Say whose line it was."""
    rig.open_turn()
    decision = rig.fire(
        "postToolUseFailure",
        post_tool_use("powershell", {"command": f"python {HARNESS} resolve-step-model"}, event_failed=True),
    )
    context = decision["additionalContext"]
    assert "REWROTE" in context and "permission layer" in context and "do not retry" in context
    assert rig.fire(
        "postToolUseFailure", post_tool_use("powershell", {"command": "git status"}, event_failed=True)
    ) is None


def test_every_session_is_told_the_rewrite_is_the_adapters() -> None:
    hint = make_invocation().hint()
    assert f"& {PYTHON} {HARNESS} resolve-step --workflow-slug <workflow-slug>" in hint
    assert "--env-file" in hint and "permission layer" in hint
