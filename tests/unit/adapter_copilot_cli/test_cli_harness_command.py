"""H4 — a harness command is a REQUEST the preToolUse hook answers itself.

The org bundle's orchestrators run `<python> <harness>/harness.py resolve-step
--workflow-slug <slug>` and `... resolve-step-model` with NO session ids, through
`powershell` (Windows) or `bash`. The hook recognizes the line strictly, runs the function
for the session the PAYLOAD names, and returns its report as the deny reason (the only
preToolUse text the runtime shows the agent). The command is never edited and the shell
never runs it; everything else that mentions `harness.py` is rejected.
"""

from __future__ import annotations

import json

import pytest

from cli_rig import (
    CWD,
    HARNESS,
    HOST,
    PYTHON,
    SUBAGENT,
    TURN_SESSION,
    FakeRunner,
    Rig,
    make_invocation,
    pre_tool_use,
)
from copilot_cli_adapter.core import HARNESS_REJECTED, HARNESS_RESULT, Adapter
from copilot_cli_adapter.payload import HookInput
from copilot_cli_adapter.renderer import ContextRenderer
from copilot_cli_adapter.runner import HarnessCommandError
from copilot_cli_adapter.tracker import SessionTracker
from harness_stub import build_error_report

FORWARD = HARNESS.replace("\\", "/")
RESOLVED = {
    "context": {"function": "resolve-step", "sessionId": TURN_SESSION},
    "outcome": {"status": "resolved"},
    "step": {"slug": "build", "agent": "system-architect"},
}


def _command(rig: Rig, command: str, tool: str = "powershell", host: str = HOST):
    return rig.fire(
        "preToolUse", pre_tool_use((tool, {"command": command, "description": "d"}), host=host)
    )


def _report_in(reason: str) -> dict:
    return json.loads(reason.split("\n\n", 1)[1])


def _harness_calls(rig: Rig) -> list[tuple[str, dict]]:
    return [call for call in rig.runner.calls if call[0] in ("resolve-step", "resolve-step-model")]


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
def test_the_hook_runs_the_request_and_returns_the_report(rig: Rig, command: str) -> None:
    rig.open_turn()
    rig.runner.script["resolve-step"] = RESOLVED
    decision = _command(rig, command)
    assert set(decision) == {"permissionDecision", "permissionDecisionReason"}
    assert decision["permissionDecision"] == "deny"
    reason = decision["permissionDecisionReason"]
    assert reason.startswith(f"{HARNESS_RESULT} (resolve-step): ")
    assert "not an error" in reason and "do not re-issue" in reason
    assert _report_in(reason) == RESOLVED
    assert _harness_calls(rig) == [
        ("resolve-step", {"workflowSlug": "pi-planning", "sessionId": TURN_SESSION, "parentSessionId": None})
    ]


def test_the_command_is_never_modified(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["resolve-step"] = RESOLVED
    for command in (
        f"python {HARNESS} resolve-step --workflow-slug x",
        f"python {HARNESS} resolve-step-model",
        f"python {HARNESS} end-session",
    ):
        rig.runner.script["resolve-step-model"] = RESOLVED
        decision = _command(rig, command)
        assert "modifiedArgs" not in decision
        assert decision["permissionDecision"] == "deny"


def test_a_turn_request_carries_the_observed_session_and_no_parent(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["resolve-step-model"] = {**RESOLVED, "context": {"function": "resolve-step-model"}}
    decision = _command(rig, f"python {HARNESS} resolve-step-model")
    assert decision["permissionDecisionReason"].startswith(f"{HARNESS_RESULT} (resolve-step-model)")
    assert _harness_calls(rig) == [("resolve-step-model", {"sessionId": TURN_SESSION, "parentSessionId": None})]


def test_attribution_comes_from_the_payload_session_and_its_tracked_parent(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.runner.script["resolve-step-model"] = RESOLVED
    _command(rig, f"python {HARNESS} resolve-step-model", host=SUBAGENT)
    assert _harness_calls(rig) == [("resolve-step-model", {"sessionId": SUBAGENT, "parentSessionId": TURN_SESSION})]
    _command(rig, f"python {HARNESS} resolve-step-model", host=HOST)
    assert _harness_calls(rig)[-1] == ("resolve-step-model", {"sessionId": TURN_SESSION, "parentSessionId": None})


def test_a_harness_error_report_is_still_the_result(rig: Rig) -> None:
    rig.open_turn()
    report = build_error_report("resolve-step", session_id=TURN_SESSION)
    rig.runner.script["resolve-step"] = report
    decision = _command(rig, f"python {HARNESS} resolve-step --workflow-slug x")
    reason = decision["permissionDecisionReason"]
    assert reason.startswith(HARNESS_RESULT)
    assert _report_in(reason) == report


def test_a_harness_refusal_is_relayed_as_a_rejection(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["resolve-step"] = HarnessCommandError(
        "harness command 'resolve-step' failed (exit 2): Traceback ...\nharness: invalid-inquiry: x",
        refusal="harness: invalid-inquiry: harness.py resolve-step: unrecognized arguments: --bogus y",
    )
    decision = _command(rig, f"python {HARNESS} resolve-step --bogus y")
    assert decision["permissionDecisionReason"] == (
        f"{HARNESS_REJECTED}: harness: invalid-inquiry: harness.py resolve-step: "
        "unrecognized arguments: --bogus y"
    )
    assert _harness_calls(rig) == [("resolve-step", {"bogus": "y", "sessionId": TURN_SESSION, "parentSessionId": None})]


@pytest.mark.parametrize(
    "failure",
    [
        HarnessCommandError("harness command 'resolve-step' failed (exit 1): Traceback secret"),
        RuntimeError("Traceback secret"),
    ],
)
def test_an_unavailable_harness_is_rejected_without_leaking_its_output(
    rig: Rig, failure: Exception
) -> None:
    rig.open_turn()
    rig.runner.script["resolve-step"] = failure
    decision = _command(rig, f"python {HARNESS} resolve-step --workflow-slug x")
    reason = decision["permissionDecisionReason"]
    assert reason.startswith(HARNESS_REJECTED) and "could not answer" in reason
    assert "secret" not in reason
    assert any("secret" in line for line in rig.diagnostics)


def test_bash_on_posix_is_answered_the_same_way(tmp_path, binding) -> None:
    (tmp_path / "i").mkdir()
    (tmp_path / "s").mkdir()
    invocation = make_invocation(
        python="/opt/h/.venv/bin/python", harness="/opt/h/harness.py", windows=False
    )
    tracker = SessionTracker(tmp_path / "state")
    runner = FakeRunner()
    runner.script["resolve-step-model"] = RESOLVED
    adapter = Adapter(
        binding, tracker, ContextRenderer(tmp_path / "i", tmp_path / "s"), runner,
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
    assert decision["permissionDecisionReason"].startswith(HARNESS_RESULT)
    assert runner.calls[-1] == ("resolve-step-model", {"sessionId": session, "parentSessionId": None})


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
        (f"$env:FRAMEWORK_DIR='x'; python {HARNESS} resolve-step", "one plain invocation"),
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
        (f"python {HARNESS} resolve-step --workflow-slug", "needs a value"),
        (f"python {HARNESS} resolve-step --workflow-slug a --workflow-slug b", "more than once"),
    ],
)
def test_everything_else_mentioning_the_harness_is_rejected_unrun(
    rig: Rig, command: str, expected: str
) -> None:
    rig.open_turn()
    decision = _command(rig, command)
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"].startswith(HARNESS_REJECTED)
    assert expected in decision["permissionDecisionReason"]
    assert _harness_calls(rig) == []


def test_a_harness_command_batched_with_another_call_is_rejected_unrun(rig: Rig) -> None:
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
    assert _harness_calls(rig) == []


def test_in_an_ungoverned_session_a_harness_command_is_denied_with_a_diagnosis(rig: Rig) -> None:
    decision = _command(rig, f"python {HARNESS} resolve-step --workflow-slug x")
    assert decision["permissionDecision"] == "deny"
    assert "trusted" in decision["permissionDecisionReason"]
    assert _command(rig, "git status --short") is None
    assert rig.runner.calls == []


def test_the_hint_says_the_shell_never_runs_the_line_and_how_the_result_arrives() -> None:
    hint = make_invocation().hint()
    assert f"& {PYTHON} {HARNESS} resolve-step --workflow-slug <workflow-slug>" in hint
    assert "does NOT run this command" in hint
    assert HARNESS_RESULT in hint and HARNESS_REJECTED in hint
    assert "--env-file" not in hint and "$env:" not in hint
    assert CWD in hint


def test_the_hint_quotes_a_path_only_when_the_request_needs_it() -> None:
    spaced = make_invocation(python=r"C:\Program Files\Python312\python.exe")
    assert r"& 'C:\Program Files\Python312\python.exe' " in spaced.hint()


def _flavored_adapter(tmp_path, binding, invocation, workspace: str):
    (tmp_path / "i").mkdir()
    (tmp_path / "s").mkdir()
    tracker, runner = SessionTracker(tmp_path / "state"), FakeRunner()
    runner.script["resolve-step"] = RESOLVED
    adapter = Adapter(
        binding, tracker, ContextRenderer(tmp_path / "i", tmp_path / "s"), runner,
        workspace, invocation, diagnostic=lambda _: None,
    )
    adapter.handle(HookInput("userPromptSubmitted", {"sessionId": "h", "prompt": "p"}, binding, "rte"))

    def ask(command: str, tool: str):
        payload = {"sessionId": "h", "cwd": workspace, "toolCalls": [
            {"name": tool, "args": {"command": command}}
        ]}
        return adapter.handle(HookInput("preToolUse", payload, binding, None)), runner

    return ask


@pytest.mark.parametrize(
    "windows, python, harness, tool",
    [
        (True, r"C:\Users\o'neil\h\.venv\Scripts\python.exe", r"C:\Users\o'neil\h\harness.py",
         "powershell"),
        (False, "/home/o'neil/h/.venv/bin/python", "/home/o'neil/h/harness.py", "bash"),
        (False, "/home/o'neil/h/py", "/home/it's'/h/harness.py'", "bash"),
    ],
    ids=["powershell-doubled-apostrophe", "posix-concatenated-apostrophe", "posix-edge-apostrophes"],
)
def test_the_hinted_line_is_accepted_when_a_path_holds_an_apostrophe(
    tmp_path, binding, windows, python, harness, tool
) -> None:
    """The hint renders `'C:\\Users\\o''neil\\...'` (PowerShell) or `'/home/o'"'"'neil/...'`
    (POSIX); following it exactly must be answered, not rejected."""
    invocation = make_invocation(python=python, harness=harness, windows=windows)
    command = invocation.example("resolve-step --workflow-slug pi-planning")
    ask = _flavored_adapter(tmp_path, binding, invocation, "/srv/org/workspace")
    decision, runner = ask(command, tool)
    assert decision["permissionDecisionReason"].startswith(HARNESS_RESULT), decision
    assert runner.calls[-1][0] == "resolve-step"
    assert runner.calls[-1][1]["workflowSlug"] == "pi-planning"


def test_a_powershell_doubled_apostrophe_is_not_decoded_for_posix(tmp_path, binding) -> None:
    """In POSIX `'o''neil'` is `oneil`, not `o'neil`: decoding it the PowerShell way would
    match a path the shell never names."""
    harness = "/home/o'neil/h/harness.py"
    invocation = make_invocation(python="/usr/bin/python3", harness=harness, windows=False)
    ask = _flavored_adapter(tmp_path, binding, invocation, "/srv/org/workspace")
    decision, runner = ask("python3 '/home/o''neil/h/harness.py' resolve-step", "bash")
    assert decision["permissionDecisionReason"].startswith(HARNESS_REJECTED)
    assert [call for call in runner.calls if call[0] == "resolve-step"] == []
