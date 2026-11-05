"""`adapter.py hook` — stdin bytes in, one JSON object (or nothing) out; fail closed."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from cli_rig import ADAPTER_DIR, HOST, ORCHESTRATOR, Rig, load_script, pre_tool_use
from model_catalogue import write_bound_catalogue, write_catalogue

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
    write_bound_catalogue(framework, ADAPTER_DIR / "models.yaml")
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
    write_bound_catalogue(framework, ADAPTER_DIR / "models.yaml")
    elsewhere = tmp_path / "portfolio-root"
    elsewhere.mkdir()
    for variable in ("FRAMEWORK_DIR", "FRAMEWORK_WORKSPACE_DIR"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path / "state"))
    adapter, binding = ENTRY.build_adapter(str(framework), str(elsewhere))
    from copilot_cli_adapter.payload import HookInput

    decision = adapter.handle(HookInput("sessionStart", {"sessionId": "x"}, binding, None))
    assert f"(FRAMEWORK_WORKSPACE_DIR): {elsewhere}" in decision["additionalContext"]


def _bootstrap_session_start(tmp_path: Path, monkeypatch, framework: Path) -> tuple[int, str, str]:
    monkeypatch.setenv("FRAMEWORK_DIR", str(framework))
    monkeypatch.delenv("FRAMEWORK_WORKSPACE_DIR", raising=False)
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path / "state"))
    return _run(["hook", "--event", "sessionStart"], {"sessionId": "x"}, ENTRY.build_adapter)


def test_a_catalogue_profile_models_yaml_does_not_bind_fails_the_load_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """Spec (core fn 4 / adapter binding): an unroutable catalogue dies at load."""
    framework = _framework(tmp_path, "# comment only\n")
    write_catalogue(framework, ["claude-haiku-4.5", "not-a-host-model"])
    code, out, err = _bootstrap_session_start(tmp_path, monkeypatch, framework)
    assert (code, out) == (2, "")
    assert "not-a-host-model" in err and "models.yaml" in err
    assert "claude-haiku-4.5" not in err.split("does not bind:")[1]


def test_a_missing_catalogue_fails_the_load_closed(tmp_path: Path, monkeypatch) -> None:
    framework = _framework(tmp_path, "# comment only\n")
    (framework / "conf" / "model-profiles.conf.yaml").unlink()
    code, out, err = _bootstrap_session_start(tmp_path, monkeypatch, framework)
    assert (code, out) == (2, "") and "model-profiles.conf.yaml" in err


def test_the_catalogue_is_rechecked_on_every_load_never_cached(
    tmp_path: Path, monkeypatch
) -> None:
    """The host's model list (and so `models.yaml`) can evolve after install: no cache."""
    framework = _framework(tmp_path, "# comment only\n")
    assert _bootstrap_session_start(tmp_path, monkeypatch, framework)[0] == 0
    write_catalogue(framework, ["claude-haiku-4.5", "retired-model"])
    code, _, err = _bootstrap_session_start(tmp_path, monkeypatch, framework)
    assert code == 2 and "retired-model" in err
    write_bound_catalogue(framework, ADAPTER_DIR / "models.yaml")
    assert _bootstrap_session_start(tmp_path, monkeypatch, framework)[0] == 0


def test_layout_overrides_reach_the_build_and_unknown_ones_fail_closed(rig: Rig) -> None:
    seen = []

    def build(*arguments):
        seen.append(arguments)
        return rig.adapter, rig.binding

    argv = ["hook", "--event", "sessionStart", "--layout", "FRAMEWORK_WORKSPACE_DIR=/ws"]
    code, _, _ = _run(argv, {"sessionId": "x"}, build)
    assert code == 0 and seen[-1][2] == {"FRAMEWORK_WORKSPACE_DIR": "/ws"}
    for bad in ("FRAMEWORK_DIR=/x", "PATH=/x", "FRAMEWORK_WORKSPACE_DIR", "FRAMEWORK_SKILLS_DIR="):
        code, out, err = _run(["hook", "--event", "preToolUse", "--layout", bad], {}, build)
        assert (code, out) == (ENTRY.EXIT_FAIL_CLOSED, "") and "--layout" in err


def test_the_default_build_honors_a_layout_override(tmp_path: Path, monkeypatch) -> None:
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
    write_bound_catalogue(framework, ADAPTER_DIR / "models.yaml")
    elsewhere = tmp_path / "portfolio-root"
    elsewhere.mkdir()
    for variable in ("FRAMEWORK_DIR", "FRAMEWORK_WORKSPACE_DIR"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("SAF_HARNESS_STATE_DIR", str(tmp_path / "state"))
    argv = [
        "hook", "--event", "sessionStart", "--framework-dir", str(framework),
        "--layout", f"FRAMEWORK_WORKSPACE_DIR={elsewhere}",
    ]
    code, out, err = _run(argv, {"sessionId": "x"}, ENTRY.build_adapter)
    assert code == 0, err
    assert f"(FRAMEWORK_WORKSPACE_DIR): {elsewhere}" in json.loads(out)["additionalContext"]
