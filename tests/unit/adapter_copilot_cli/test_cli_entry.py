"""`adapter.py hook` — stdin bytes in, one JSON object (or nothing) out; fail closed."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cli_rig import HOST, ORCHESTRATOR, Rig, load_script, pre_tool_use

ENTRY = load_script("copilot_cli_adapter_entry", "adapter.py")


def _run(argv, payload, build):
    stdin = io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ENTRY.run_hook(argv, stdin, stdout, stderr, build=build)
    return code, stdout.getvalue(), stderr.getvalue()


def _broken(*_):
    raise RuntimeError("layout exploded")


def test_a_decision_is_one_json_line(rig: Rig) -> None:
    rig.open_turn()
    build = lambda *_: (rig.adapter, rig.binding)  # noqa: E731
    payload = pre_tool_use(("create", {"file_text": "x"}))
    code, out, _ = _run(["hook", "--event", "preToolUse"], payload, build)
    assert code == 0 and out.endswith("\n") and out.count("\n") == 1
    assert json.loads(out)["permissionDecision"] == "deny"


def test_a_bom_prefixed_payload_is_read(rig: Rig) -> None:
    build = lambda *_: (rig.adapter, rig.binding)  # noqa: E731
    raw = b"\xef\xbb\xbf" + json.dumps({"sessionId": HOST, "prompt": "go"}).encode()
    code, _, err = _run(["hook", "--event", "userPromptSubmitted", "--agent", ORCHESTRATOR], raw, build)
    assert code == 0 and err == ""
    assert rig.tracker.read(HOST) is not None


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b"\xff\xfe"])
def test_an_unreadable_pre_tool_use_payload_fails_closed(rig: Rig, raw: bytes) -> None:
    build = lambda *_: (rig.adapter, rig.binding)  # noqa: E731
    code, out, err = _run(["hook", "--event", "preToolUse"], raw, build)
    assert (code, out) == (ENTRY.EXIT_FAIL_CLOSED, "") and "failing closed" in err


@pytest.mark.parametrize(
    "event", ["sessionStart", "userPromptSubmitted", "preToolUse", "postToolUse", "agentStop"]
)
def test_a_broken_bootstrap_exits_2_at_every_event(event: str) -> None:
    payload = pre_tool_use(("create", {"path": "C:\\a.md"}))
    code, out, err = _run(["hook", "--event", event], payload, _broken)
    assert (code, out) == (2, "")
    assert "layout exploded" in err and "failing closed" in err


def test_a_broken_bootstrap_denies_even_a_session_without_a_record(tmp_path, monkeypatch) -> None:
    # A broken bootstrap is what keeps H0 from creating the record: no record is no proof.
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path))
    code, _, _ = _run(
        ["hook", "--event", "preToolUse"], pre_tool_use(("view", {"path": "C:\\a"})), _broken
    )
    assert code == 2


def _framework(tmp_path: Path, env_text: str | None) -> Path:
    framework = tmp_path / "fw"
    framework.mkdir()
    if env_text is not None:
        (framework / ".env").write_text(env_text, encoding="utf-8")
    return framework


@pytest.mark.parametrize(
    ("framework_arg", "env_text", "needle"),
    [
        (None, "", "no framework root"),
        ("missing", "", "does not exist"),
        ("fw", "FRAMEWORK_WORKSPACE_DIR=portfolio\nnot a key value line\n", "line 2 is malformed"),
        ("fw", "=orphan\n", "line 1 is malformed"),
    ],
)
def test_the_real_bootstrap_refuses_a_bad_layout(
    tmp_path: Path, monkeypatch, framework_arg, env_text, needle
) -> None:
    for variable in ("FRAMEWORK_DIR", "FRAMEWORK_WORKSPACE_DIR"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path / "state"))
    _framework(tmp_path, env_text)
    argv = ["hook", "--event", "preToolUse"]
    if framework_arg is not None:
        argv += ["--framework-dir", str(tmp_path / framework_arg)]
    code, out, err = _run(argv, pre_tool_use(("view", {"path": "C:\\a"})), ENTRY.build_adapter)
    assert (code, out) == (2, "") and needle in err


def test_the_framework_dir_env_is_the_bootstrap_anchor(tmp_path: Path, monkeypatch) -> None:
    framework = _framework(tmp_path, "# comment only\n\n")
    monkeypatch.setenv("FRAMEWORK_DIR", str(framework))
    monkeypatch.delenv("FRAMEWORK_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path / "state"))
    code, out, err = _run(
        ["hook", "--event", "sessionStart"], {"sessionId": "x"}, ENTRY.build_adapter
    )
    assert code == 0 and err == "" and str(framework) in json.loads(out)["additionalContext"]


def test_the_default_build_wires_the_workspace_override(tmp_path: Path, monkeypatch) -> None:
    framework = tmp_path / "fw"
    for name in ("agents", "artifacts", "instructions", "skills", "templates", "workflows"):
        (framework / name).mkdir(parents=True)
    (framework / ".env").write_text(
        "\n".join(
            f"FRAMEWORK_{name.upper()}_DIR={name}"
            for name in ("agents", "artifacts", "instructions", "skills", "templates", "workflows", "workspace")
        ),
        encoding="utf-8",
    )
    elsewhere = tmp_path / "portfolio-root"
    elsewhere.mkdir()
    for variable in ("FRAMEWORK_DIR", "FRAMEWORK_WORKSPACE_DIR"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path / "state"))
    adapter, binding = ENTRY.build_adapter(str(framework), str(elsewhere))
    from copilot_cli_adapter.payload import HookInput

    decision = adapter.handle(HookInput("sessionStart", {"sessionId": "x"}, binding, None))
    assert f"(FRAMEWORK_WORKSPACE_DIR): {elsewhere}" in decision["additionalContext"]
