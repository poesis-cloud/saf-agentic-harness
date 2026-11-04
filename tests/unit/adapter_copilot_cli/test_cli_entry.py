"""`adapter.py hook` — stdin bytes in, one JSON object (or nothing) out; fail closed."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cli_rig import HOST, ORCHESTRATOR, Rig, load_script, pre_tool_use
from copilot_cli_adapter.tracker import SessionRecord, SessionTracker

ENTRY = load_script("copilot_cli_adapter_entry", "adapter.py")


def _run(argv, payload, build, may_be_governed=None):
    stdin = io.BytesIO(payload if isinstance(payload, bytes) else json.dumps(payload).encode())
    stdout, stderr = io.StringIO(), io.StringIO()
    code = ENTRY.run_hook(
        argv, stdin, stdout, stderr, build=build, may_be_governed=may_be_governed
    )
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
def test_an_unreadable_pre_tool_use_payload_is_denied(rig: Rig, raw: bytes) -> None:
    build = lambda *_: (rig.adapter, rig.binding)  # noqa: E731
    code, out, err = _run(["hook", "--event", "preToolUse"], raw, build)
    assert code == 0 and json.loads(out)["permissionDecision"] == "deny"
    assert err


def test_other_events_never_fail_the_host(rig: Rig) -> None:
    code, out, err = _run(["hook", "--event", "postToolUse"], b"not json", _broken)
    assert (code, out) == (0, "") and "layout exploded" not in out and err


def test_a_broken_install_denies_only_sessions_that_may_be_governed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path))
    payload = pre_tool_use(("create", {"path": "C:\\a.md"}))
    code, out, err = _run(["hook", "--event", "preToolUse"], payload, _broken)
    assert (code, out) == (0, "") and "layout exploded" in err
    SessionTracker(tmp_path / "github-copilot-cli").write(
        HOST, SessionRecord("s-t1", ORCHESTRATOR, "turn")
    )
    code, out, _ = _run(["hook", "--event", "preToolUse"], payload, _broken)
    reason = json.loads(out)["permissionDecisionReason"]
    assert "RuntimeError" in reason and "layout exploded" not in reason


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
