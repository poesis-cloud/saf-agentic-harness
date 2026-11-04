"""`github-copilot-cli` end to end through real processes: runtime payload → adapter → harness."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest

from harness_stub import HarnessStub, build_report

REPO_ROOT = Path(__file__).resolve().parents[3]
STUB_ENTRYPOINT = REPO_ROOT / "tests" / "functional" / "adapter" / "stub_harness_entrypoint.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "copilot-app-windows-1.0.102.jsonl"
LAYOUT = ("agents", "artifacts", "instructions", "skills", "templates", "workflows", "workspace")
ORCHESTRATOR = "release-train-engineer"
HOST = "1d793d07-adc6-4f25-bec3-5b03b44e949e"
SUBAGENT = "ecdda9f8-7300-43cc-9fae-ad08c0456604"


@dataclass(frozen=True)
class Run:
    code: int
    stdout: str
    stderr: str

    @property
    def decision(self) -> dict[str, Any] | None:
        return json.loads(self.stdout) if self.stdout.strip() else None


@dataclass
class Host:
    root: Path
    environment: dict[str, str]
    stub: HarnessStub

    @property
    def adapter(self) -> Path:
        return self.root / "adapters" / "github-copilot-cli" / "adapter.py"

    @property
    def workspace(self) -> Path:
        return self.root / "workspace"

    def fire(self, event: str, payload: Any, agent: str | None = None) -> Run:
        argv = [sys.executable, str(self.adapter), "hook", "--event", event,
                "--framework-dir", str(self.root)]
        if agent:
            argv += ["--agent", agent]
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        done = subprocess.run(
            argv, input=data, capture_output=True, env=self.environment, cwd=self.root, timeout=60
        )
        return Run(done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8"))


@pytest.fixture
def host(tmp_path: Path) -> Host:
    root = tmp_path / "framework"
    root.mkdir()
    shutil.copytree(
        REPO_ROOT / "adapters" / "github-copilot-cli",
        root / "adapters" / "github-copilot-cli",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(STUB_ENTRYPOINT, root / "harness.py")
    for name in LAYOUT:
        (root / name).mkdir()
    (root / "instructions" / "reports-handling.instructions.md").write_text("x\n", encoding="utf-8")
    (root / "skills" / "code-review.skill.md").write_text("# Code review\n", encoding="utf-8")
    (root / ".env").write_text(
        "".join(f"FRAMEWORK_{name.upper()}_DIR={name}\n" for name in LAYOUT), encoding="utf-8"
    )
    stub = HarnessStub.create(tmp_path / "script.json", tmp_path / "journal.jsonl")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "HOME": str(tmp_path / "home"),
        "SAF_HARNESS_STATE_DIR": str(tmp_path / "state"),
        "HARNESS_STUB_SCRIPT": str(stub.script_path),
        "HARNESS_STUB_JOURNAL": str(stub.journal_path),
    }
    return Host(root=root, environment=environment, stub=stub)


def _captured() -> list[dict[str, Any]]:
    return [json.loads(line) for line in FIXTURES.read_text(encoding="utf-8").splitlines() if line]


def test_the_captured_app_session_replays_silently_when_ungoverned(host: Host) -> None:
    """No orchestrator scoped hook fired: every global hook passes, no harness call is made."""
    for record in _captured():
        run = host.fire(record["hookType"], record["input"])
        assert run.code == 0, run.stderr
        if record["hookType"] == "sessionStart":
            assert "harness.py" in run.decision["additionalContext"]
        else:
            assert run.decision is None, (record["hookType"], run.stdout)
    assert host.stub.functions == ()


def test_a_governed_turn_runs_every_boundary(host: Host) -> None:
    ws = host.workspace
    artifact = str(ws / "portfolio" / "epic.md")
    assert host.fire(
        "userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1, "cwd": str(host.root)},
        ORCHESTRATOR,
    ).code == 0
    assert host.stub.functions[-1] == "start-session"

    dispatch = host.fire("preToolUse", {"sessionId": HOST, "cwd": str(host.root), "toolCalls": [
        {"id": "t1", "name": "task", "args": {"agent_type": "product-manager", "prompt": "p"}}]})
    assert dispatch.decision is None, dispatch.stdout
    assert "check-step-preconditions" in host.stub.functions

    assert host.fire("subagentStart", {"sessionId": HOST, "agentId": SUBAGENT,
                                        "agentType": "product-manager", "timestamp": 2}).code == 0
    write = {"sessionId": SUBAGENT, "agentId": SUBAGENT, "agentType": "product-manager",
             "cwd": str(host.root), "toolCalls": [
                 {"id": "t2", "name": "create", "args": {"path": artifact, "file_text": "x"}}]}
    assert host.fire("preToolUse", write).decision is None
    (authorize,) = host.stub.find_calls("check-step-authorization")
    assert authorize.value("--artifact-path") == "portfolio/epic.md"

    host.stub.answers(
        "check-step-authorization",
        build_report("check-step-authorization", "denied", authorization={
            "actor": "product-manager", "artifactPath": "portfolio/epic.md", "action": "create",
            "resource": "epic", "failureMessage": "not this step's"}),
    )
    denied = host.fire("preToolUse", write).decision
    assert denied["permissionDecision"] == "deny" and "not this step's" in denied["permissionDecisionReason"]

    host.fire("postToolUse", {"sessionId": SUBAGENT, "timestamp": 3, "toolName": "create",
                              "toolArgs": {"path": artifact, "file_text": "x"},
                              "toolResult": {"resultType": "success", "textResultForLlm": "ok"}})
    assert "check-step-artifact" in host.stub.functions

    host.fire("subagentStop", {"sessionId": HOST, "agentId": SUBAGENT, "agentType": "product-manager",
                               "stopReason": "end_turn", "timestamp": 4})
    host.fire("postToolUse", {"sessionId": HOST, "timestamp": 5, "toolName": "task",
                              "toolArgs": {"agent_type": "product-manager", "prompt": "p"},
                              "toolResult": {"resultType": "success", "textResultForLlm": "[elided]"}})
    assert "check-step-postconditions" in host.stub.functions
    host.fire("agentStop", {"sessionId": HOST, "stopReason": "end_turn", "timestamp": 6})
    assert host.stub.functions[-1] == "end-session"


def test_an_unreadable_payload_in_a_governed_session_is_denied(host: Host) -> None:
    host.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    run = host.fire("preToolUse", {"sessionId": HOST, "toolCalls": "nope"})
    assert run.code == 0 and run.decision["permissionDecision"] == "deny"
    run = host.fire("preToolUse", b"\x00 not json")
    assert run.code == 0 and run.decision["permissionDecision"] == "deny"


def test_an_unextractable_write_is_denied(host: Host) -> None:
    host.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    run = host.fire("preToolUse", {"sessionId": HOST, "toolCalls": [
        {"id": "a", "name": "view", "args": {"path": "C:\\x"}},
        {"id": "b", "name": "apply_patch", "args": "garbage without headers"}]})
    assert run.decision["permissionDecision"] == "deny"
    assert "check-step-authorization" not in host.stub.functions


def test_the_harness_command_is_rewritten_to_the_installed_interpreter(host: Host) -> None:
    host.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    harness = host.root / "harness.py"
    tool = "powershell" if os.name == "nt" else "bash"
    run = host.fire("preToolUse", {"sessionId": HOST, "toolCalls": [{"id": "c", "name": tool, "args": {
        "command": f"python {harness} resolve-step --workflow-slug pi-planning"}}]})
    command = run.decision["modifiedArgs"]["command"]
    assert sys.executable in command and "--session-id" in command
    assert "FRAMEWORK_DIR" in command and "--workflow-slug pi-planning" in command


def test_a_harness_failure_never_leaks_its_text(host: Host) -> None:
    host.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    host.fire("subagentStart", {"sessionId": HOST, "agentId": SUBAGENT, "agentType": "pm", "timestamp": 2})
    host.stub.fails("check-step-authorization", "Traceback: secret C:\\Users\\x")
    run = host.fire("preToolUse", {"sessionId": SUBAGENT, "toolCalls": [{"id": "w", "name": "edit", "args": {
        "path": str(host.workspace / "portfolio" / "a.md"), "old_str": "a", "new_str": "b"}}]})
    assert run.decision["permissionDecision"] == "deny"
    assert "secret" not in run.decision["permissionDecisionReason"]
