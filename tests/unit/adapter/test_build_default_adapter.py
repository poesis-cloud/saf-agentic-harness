"""Unit tests for `build_default_adapter` — the adapter's own composition root.

Every other adapter test injects its collaborators, so the composition point itself was
never exercised: the paths it derives from the environment (the harness entrypoint the
command runner execs, the runtime record the tracker keeps) were asserted by nobody.
These tests drive the REAL `build_default_adapter()` through the real host entry, with
only the process boundary faked, so a wrong path fails here instead of at a deployment.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest

import command_runner
import adapter
from adapter import AdapterBootstrapError, build_default_adapter, run_hook_entry
from conftest import build_report

HOST_SESSION = "chat-session-guid"
TURN_SESSION = "chat-session-guid-t2026-07-11t14-32-07-000z"
TIMESTAMP = "2026-07-11T14:32:07.000Z"
INSTRUCTION_BODY = "Never surface step details to the user."


@pytest.fixture
def framework_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Deploy the framework the way the spec mandates: its OWN root, not the harness's."""
    framework = tmp_path / "saf-agentic-organization"
    for directory in ("workspace", "instructions", "skills"):
        (framework / directory).mkdir(parents=True)
    (framework / "instructions" / "reports-handling.instructions.md").write_text(
        f"{INSTRUCTION_BODY}\n", encoding="utf-8"
    )
    monkeypatch.setenv("FRAMEWORK_DIR", str(framework))
    monkeypatch.setenv("FRAMEWORK_WORKSPACE_DIR", "workspace")
    monkeypatch.setenv("FRAMEWORK_INSTRUCTIONS_DIR", "instructions")
    monkeypatch.setenv("FRAMEWORK_SKILLS_DIR", "skills")
    return framework


@pytest.fixture
def recorded_argv(monkeypatch: pytest.MonkeyPatch) -> list[Sequence[str]]:
    """Fake ONLY the process boundary, so the real composition root is what is measured."""
    calls: list[Sequence[str]] = []
    reports: Mapping[str, Mapping[str, Any]] = {
        "start-session": build_report(
            "start-session",
            "started",
            session={"sessionId": "session-a", "agent": "product-manager"},
        ),
        "resolve-workflow-instructions": build_report(
            "resolve-workflow-instructions",
            "resolved",
            instructions=["reports-handling"],
        ),
        "resolve-workflow-skills": build_report(
            "resolve-workflow-skills", "resolved", skills=["code-review"]
        ),
    }

    def _run_process(argv: Sequence[str]) -> tuple[int, str, str]:
        calls.append(list(argv))
        return 0, json.dumps(reports[argv[2]]), ""

    monkeypatch.setattr(command_runner, "run_subprocess", _run_process)
    return calls


def _fire_session_started() -> str:
    """Fire one real H0 host event through the real host entry, answering its stdout."""
    payload: dict[str, Any] = {
        "timestamp": TIMESTAMP,
        "hook_event_name": "UserPromptSubmit",
        "session_id": HOST_SESSION,
        "prompt": "…the user's message…",
    }
    written: list[str] = []
    exit_code = run_hook_entry(
        argv=["hook", "--event", "UserPromptSubmit", "--agent", "product-manager"],
        stdin_text=json.dumps(payload),
        write_stdout=written.append,
        build_adapter=build_default_adapter,
    )
    assert exit_code == 0
    return "".join(written)


def test_harness_entrypoint_resolves_inside_the_harness_repo(
    repo_root: Path, framework_dir: Path, recorded_argv: list[Sequence[str]]
) -> None:
    """Spec (adapter, I15): the adapter's one edge is the harness's command API.

    `harness.py` is the HARNESS repo's entrypoint while `FRAMEWORK_DIR` names the
    FRAMEWORK repo, so the entrypoint must be derived from the adapter's own location.
    Derived from `FRAMEWORK_DIR` it exists in no mandated deployment, every invocation
    raises `HarnessCommandError`, and H2/H3 render that as a denial: the adapter fails
    closed on everything.
    """
    _fire_session_started()

    entrypoint = Path(recorded_argv[0][1])
    assert entrypoint == repo_root / "harness.py"
    assert entrypoint.exists()
    assert framework_dir not in entrypoint.parents


def test_session_record_is_written_outside_every_repository(
    adapter_dir: Path,
    repo_root: Path,
    tmp_path: Path,
    framework_dir: Path,
    recorded_argv: list[Sequence[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec (adapter, Session identity binding): the tracker is the adapter's own record.

    It is per-user RUNTIME state, not source: written into the adapter directory it
    dirties a version-controlled checkout on every turn and breaks outright on a
    read-only or shared one. The governed workspace is equally wrong — the Git plane
    ring-fences it to artifacts plus logs.
    """
    state_home = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))

    _fire_session_started()

    record = (
        state_home
        / "saf-agentic-harness"
        / "vscode-github-copilot-chat"
        / "session-tracker.json"
    )
    assert json.loads(record.read_text(encoding="utf-8")) == {
        HOST_SESSION: [TURN_SESSION]
    }
    assert not (adapter_dir / ".session-tracker.json").exists()
    assert not list(repo_root.rglob("*session-tracker*.json"))
    assert not list(framework_dir.rglob("*session-tracker*"))


def test_session_record_falls_back_to_the_default_state_home(
    tmp_path: Path,
    framework_dir: Path,
    recorded_argv: list[Sequence[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec (adapter, Session identity binding): the record is per-user runtime state.

    With no `XDG_STATE_HOME` declared it belongs under the user's default state home —
    still outside every repository, and never a path the framework or host chooses.
    """
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    _fire_session_started()

    record = (
        tmp_path
        / "home"
        / ".local"
        / "state"
        / "saf-agentic-harness"
        / "vscode-github-copilot-chat"
        / "session-tracker.json"
    )
    assert record.exists()


def test_layout_directories_still_come_from_the_framework(
    framework_dir: Path, recorded_argv: list[Sequence[str]]
) -> None:
    """Spec (adapter, H0 Output construction): refs resolve under the framework layout.

    Correcting the entrypoint must not drag the layout along: the instruction inlined and
    the skill load directive rendered still come from the directories `FRAMEWORK_DIR`
    declares, not from the harness repo.
    """
    stdout = _fire_session_started()

    injected = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
    assert INSTRUCTION_BODY in injected
    assert str(framework_dir / "skills" / "code-review.skill.md") in injected


def test_the_harness_runs_under_the_adapter_own_interpreter(
    framework_dir: Path, recorded_argv: list[Sequence[str]]
) -> None:
    """Spec (adapter, Rendered registration — bootstrap): the harness command runs under
    the SAME interpreter the hook entry bootstrapped, never a `python3` inherited from
    whatever PATH the host process happens to carry (which may lack the dependencies).
    """
    _fire_session_started()

    assert recorded_argv[0][0] == sys.executable


def test_refuses_to_bootstrap_without_a_framework_dir(
    framework_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec (adapter, Bootstrap failure): `FRAMEWORK_DIR` is the one anchor every layout
    path derives from; guessing it (e.g. the harness checkout) would govern the wrong
    tree, so its absence is a bootstrap refusal, never a fallback.
    """
    monkeypatch.delenv("FRAMEWORK_DIR")

    with pytest.raises(AdapterBootstrapError, match="FRAMEWORK_DIR"):
        build_default_adapter()


def test_reads_the_layout_from_the_framework_env_file_when_the_hook_env_carries_none(
    framework_dir: Path,
    recorded_argv: list[Sequence[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spec (adapter, Bootstrap): the rendered hook carries only `FRAMEWORK_DIR`; the
    layout variables come from `FRAMEWORK_DIR/.env` exactly as the core loads them (the
    process environment taking precedence) — never from a developer's exported shell.
    """
    for variable in (
        "FRAMEWORK_WORKSPACE_DIR",
        "FRAMEWORK_INSTRUCTIONS_DIR",
        "FRAMEWORK_SKILLS_DIR",
    ):
        monkeypatch.delenv(variable)
    (framework_dir / ".env").write_text(
        "# layout\nFRAMEWORK_WORKSPACE_DIR=workspace\n"
        "FRAMEWORK_INSTRUCTIONS_DIR=instructions\nFRAMEWORK_SKILLS_DIR=skills\n",
        encoding="utf-8",
    )

    stdout = _fire_session_started()

    injected = json.loads(stdout)["hookSpecificOutput"]["additionalContext"]
    assert INSTRUCTION_BODY in injected
    assert str(framework_dir / "skills" / "code-review.skill.md") in injected


def test_the_process_environment_takes_precedence_over_the_env_file(
    framework_dir: Path, recorded_argv: list[Sequence[str]]
) -> None:
    """Spec (adapter, Bootstrap): as in the core config loader, a variable the process
    environment declares wins over the `.env` file's.
    """
    (framework_dir / ".env").write_text(
        "FRAMEWORK_INSTRUCTIONS_DIR=nowhere\n", encoding="utf-8"
    )

    stdout = _fire_session_started()

    assert INSTRUCTION_BODY in json.loads(stdout)["hookSpecificOutput"]["additionalContext"]


def test_a_malformed_env_file_is_a_bootstrap_refusal(framework_dir: Path) -> None:
    """Spec (adapter, Bootstrap failure): an unreadable layout is refused, not guessed."""
    (framework_dir / ".env").write_text("not a key value line\n", encoding="utf-8")

    with pytest.raises(AdapterBootstrapError, match=".env"):
        build_default_adapter()


def test_the_host_entry_fails_closed_with_a_visible_diagnostic(
    framework_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Spec (adapter, Bootstrap failure): an adapter that cannot bootstrap answers exit 2
    with its diagnostic on stderr — the host's one blocking channel — never a crash exit
    the host would treat as non-blocking and fail open on.
    """
    monkeypatch.delenv("FRAMEWORK_DIR")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "timestamp": TIMESTAMP,
                    "hook_event_name": "UserPromptSubmit",
                    "session_id": HOST_SESSION,
                    "prompt": "…",
                }
            )
        ),
    )

    exit_code = adapter.main(
        ["hook", "--event", "UserPromptSubmit", "--agent", "product-manager"]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "FRAMEWORK_DIR" in captured.err
