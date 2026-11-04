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

from harness_stub import HarnessStub, build_error_report, build_report
from model_catalogue import write_bound_catalogue, write_catalogue

REPO_ROOT = Path(__file__).resolve().parents[3]
STUB_ENTRYPOINT = REPO_ROOT / "tests" / "functional" / "adapter" / "stub_harness_entrypoint.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "copilot-app-windows-1.0.102.jsonl"
# The standalone CLI (Linux, `copilot -p`, org bundle loaded with `--plugin-dir`): singular
# preToolUse shape and plugin-namespaced agent identities.
CLI_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "copilot-cli-linux-1.0.105.jsonl"
CLI_FRAMEWORK = "/home/user/saf-agentic-organization"
CLI_HARNESS = "/home/user/saf-agentic-harness/harness.py"
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
    # The binding is contract-validated at load by the shared validator, which reads the
    # adapter contracts the harness ships beside `adapters/`: the copy carries both.
    shutil.copy2(REPO_ROOT / "adapters" / "binding_contract.py", root / "adapters")
    shutil.copytree(
        REPO_ROOT / "contracts" / "conf" / "adapters", root / "contracts" / "conf" / "adapters"
    )
    shutil.copy2(STUB_ENTRYPOINT, root / "harness.py")
    write_bound_catalogue(root, root / "adapters" / "github-copilot-cli" / "models.yaml")
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


def _captured(fixtures: Path = FIXTURES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in fixtures.read_text(encoding="utf-8").splitlines() if line]


def _relocated(value: Any, host: Host) -> Any:
    """Answer a captured CLI payload with its machine paths moved onto this test host."""
    if isinstance(value, dict):
        return {key: _relocated(item, host) for key, item in value.items()}
    if isinstance(value, list):
        return [_relocated(item, host) for item in value]
    if isinstance(value, str):
        return value.replace(CLI_HARNESS, str(host.root / "harness.py")).replace(
            CLI_FRAMEWORK, str(host.root)
        )
    return value


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


def test_the_captured_standalone_cli_session_replays_silently_when_ungoverned(host: Host) -> None:
    for record in _captured(CLI_FIXTURES):
        run = host.fire(record["hookType"], _relocated(record["input"], host))
        assert run.code == 0, run.stderr
        if record["hookType"] == "sessionStart":
            assert "harness.py" in run.decision["additionalContext"]
        elif run.decision is not None:
            # The one harness request of the capture: outside a governed session it is refused.
            assert run.decision["permissionDecision"] == "deny"
            assert "no governed harness session" in run.decision["permissionDecisionReason"]
    assert host.stub.functions == ()


def test_the_captured_standalone_cli_session_is_governed_through_plugin_agent_names(
    host: Host,
) -> None:
    """Live CLI capture: singular preToolUse, and a plugin agent named '<plugin>:<agent>'."""
    records = [
        (record["hookType"], _relocated(record["input"], host))
        for record in _captured(CLI_FIXTURES)
    ]
    main = records[0][1]["sessionId"]
    opened = host.fire("userPromptSubmitted", {**records[1][1]}, ORCHESTRATOR)
    assert opened.code == 0 and host.stub.functions[-1] == "start-session"
    outcomes = {}
    for event, payload in records:
        run = host.fire(event, payload)
        assert run.code == 0, run.stderr
        outcomes.setdefault(event, []).append(run.decision)
    harness_reply, dispatch, *_ = outcomes["preToolUse"]
    assert harness_reply["permissionDecisionReason"].startswith("SAFe agentic harness RESULT")
    assert dispatch is None
    (resolve,) = host.stub.find_calls("resolve-step")
    assert resolve.value("--session-id").startswith(main)
    step_open = host.stub.find_calls("start-session")[-1]
    assert step_open.value("--agent") == "product-manager"
    assert step_open.value("--parent-session-id").startswith(main)
    authorized = [call.value("--artifact-path")
                  for call in host.stub.find_calls("check-step-authorization")]
    assert authorized[0] == "portfolio/products/acme/product-vision.md"
    assert len(authorized) == 2
    assert "check-step-postconditions" in host.stub.functions
    assert host.stub.functions[-1] == "end-session"


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
    # Unparsable stdin escapes the hook handling: exit 2, which the runtime turns into a deny.
    run = host.fire("preToolUse", b"\x00 not json")
    assert (run.code, run.stdout) == (2, "") and "failing closed" in run.stderr


def test_a_malformed_framework_env_fails_closed(host: Host) -> None:
    (host.root / ".env").write_text("FRAMEWORK_WORKSPACE_DIR\n", encoding="utf-8")
    run = host.fire("preToolUse", {"sessionId": HOST, "toolCalls": [
        {"id": "a", "name": "view", "args": {"path": "C:\\x"}}]})
    assert (run.code, run.stdout) == (2, "") and "malformed" in run.stderr


def test_a_catalogue_profile_the_binding_does_not_bind_fails_every_hook_closed(host: Host) -> None:
    """Core fn 4: an unroutable catalogue dies at load — on every hook, never cached."""
    write_catalogue(host.root, ["claude-haiku-4.5", "not-a-host-model"])
    run = host.fire("sessionStart", {"sessionId": HOST, "source": "new", "timestamp": 1})
    assert run.code == 2 and run.stdout == ""
    assert "not-a-host-model" in run.stderr and "models.yaml" in run.stderr
    assert host.stub.calls == ()


def test_an_unextractable_write_is_denied(host: Host) -> None:
    host.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    run = host.fire("preToolUse", {"sessionId": HOST, "toolCalls": [
        {"id": "a", "name": "view", "args": {"path": "C:\\x"}},
        {"id": "b", "name": "apply_patch", "args": "garbage without headers"}]})
    assert run.decision["permissionDecision"] == "deny"
    assert "check-step-authorization" not in host.stub.functions


def test_the_hook_runs_the_harness_request_itself_and_returns_its_report(host: Host) -> None:
    host.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    report = build_error_report("resolve-step", session_id=f"{HOST}-t1")
    host.stub.answers("resolve-step", report)
    host.stub.forget_calls()
    harness = host.root / "harness.py"
    tool = "powershell" if os.name == "nt" else "bash"
    command = f"python {harness} resolve-step --workflow-slug pi-planning"
    run = host.fire("preToolUse", {"sessionId": HOST, "toolCalls": [{"id": "c", "name": tool, "args": {
        "command": command, "description": "d"}}]})
    assert run.code == 0
    assert set(run.decision) == {"permissionDecision", "permissionDecisionReason"}
    assert run.decision["permissionDecision"] == "deny"
    reason = run.decision["permissionDecisionReason"]
    assert reason.startswith("SAFe agentic harness RESULT (resolve-step)")
    assert json.loads(reason.split("\n\n", 1)[1]) == report
    (call,) = host.stub.calls
    assert call.function == "resolve-step"
    assert call.value("--session-id") == f"{HOST}-t1"
    assert call.value("--workflow-slug") == "pi-planning"


def test_a_rejected_harness_request_never_reaches_the_harness(host: Host) -> None:
    host.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    host.stub.forget_calls()
    harness = host.root / "harness.py"
    run = host.fire("preToolUse", {"sessionId": HOST, "toolCalls": [{"id": "c", "name": "powershell", "args": {
        "command": f"python {harness} resolve-step --session-id forged"}}]})
    assert run.decision["permissionDecisionReason"].startswith("SAFe agentic harness REJECTED")
    assert host.stub.calls == ()


def test_a_harness_failure_never_leaks_its_text(host: Host) -> None:
    host.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    host.fire("subagentStart", {"sessionId": HOST, "agentId": SUBAGENT, "agentType": "pm", "timestamp": 2})
    host.stub.fails("check-step-authorization", "Traceback: secret C:\\Users\\x")
    run = host.fire("preToolUse", {"sessionId": SUBAGENT, "toolCalls": [{"id": "w", "name": "edit", "args": {
        "path": str(host.workspace / "portfolio" / "a.md"), "old_str": "a", "new_str": "b"}}]})
    assert run.decision["permissionDecision"] == "deny"
    assert "secret" not in run.decision["permissionDecisionReason"]


def test_a_step_an_orchestrator_subagent_dispatches_opens_under_that_subagent(host: Host) -> None:
    """Live-trial replay (app 1.0.102, plugin agents): VMO subagent dispatches a VMO-acted step.

    The nested `subagentStart` carries a `sessionId` no other event names; the dispatcher is
    only in `parentAgentId`. The step opens under the dispatcher's session and the actor's
    scoped H0 never re-opens it as a root orchestrator turn.
    """
    vmo, plugin_vmo = "value-management-officer", "poesis-saf-agentic-organization:value-management-officer"
    root, d1 = "95fd9c8d-1900-48cf-96c7-3c0a9d816674", "d1fa24bb-97fd-4123-98d3-751ba40f3fa1"
    step, opaque = "8e202f77-4865-4162-a74e-90d3273f72b6", "7abc93fb-b994-4ddc-bdf1-b567b3005b1d"
    cwd, artifact = str(host.root), str(host.workspace / "portfolio" / "portfolio-sync.md")
    start = {"transcriptPath": f"{root}\\events.jsonl", "agentType": plugin_vmo,
             "agentName": plugin_vmo, "agentDisplayName": vmo, "cwd": cwd}
    inside = {"agentType": plugin_vmo, "cwd": cwd}

    def in_d1(**fields: Any) -> dict[str, Any]:
        return {"sessionId": d1, "agentId": d1, "parentAgentId": root, **inside, **fields}

    def in_step(**fields: Any) -> dict[str, Any]:
        return {"sessionId": step, "agentId": step, "parentAgentId": d1, **inside, **fields}

    nested_start = {**start, "sessionId": opaque, "agentId": step, "timestamp": 1793825897288,
                    "parentAgentId": d1}
    events: list[tuple[str, dict[str, Any], str | None]] = [
        ("subagentStart", {**start, "sessionId": root, "agentId": d1, "timestamp": 1793825800000,
                           "parentAgentId": root}, None),
        ("userPromptSubmitted", in_d1(prompt="sync", timestamp=1793825800100), vmo),
        ("preToolUse", in_d1(timestamp=1793825890000, toolCalls=[
            {"id": "t1", "name": "task", "args": {"agent_type": plugin_vmo, "prompt": "p"}}]), None),
        ("subagentStart", nested_start, None),
        ("userPromptSubmitted", in_step(prompt="act", timestamp=1793825897400), vmo),
        ("preToolUse", in_step(timestamp=1793825898000, toolCalls=[
            {"id": "t2", "name": "create", "args": {"path": artifact, "file_text": "x"}}]), None),
        ("postToolUse", in_step(timestamp=1793825899000, toolName="create",
                                toolArgs={"path": artifact, "file_text": "x"},
                                toolResult={"resultType": "success", "textResultForLlm": "ok"}), None),
        ("agentStop", in_step(stopReason="end_turn", timestamp=1793825899500), None),
        ("subagentStop", {**nested_start, "stopReason": "end_turn"}, None),
        ("postToolUse", in_d1(timestamp=1793825899900, toolName="task",
                              toolArgs={"agent_type": plugin_vmo, "prompt": "p"},
                              toolResult={"resultType": "success", "textResultForLlm": "done"}), None),
    ]
    for event, payload, agent in events:
        run = host.fire(event, payload, agent)
        assert run.code == 0, (event, run.stderr)
        if event == "preToolUse":
            assert run.decision is None, (event, run.stdout)

    orchestrator_open, step_open = host.stub.find_calls("start-session")
    orchestrator = orchestrator_open.value("--session-id")
    assert orchestrator.startswith(d1) and orchestrator_open.value("--parent-session-id") is None
    assert step_open.value("--session-id") == step
    assert step_open.value("--agent") == vmo
    assert step_open.value("--parent-session-id") == orchestrator
    assert len(host.stub.find_calls("resolve-workflow-instructions")) == 1
    (authorize,) = host.stub.find_calls("check-step-authorization")
    assert authorize.value("--session-id") == step
    assert authorize.value("--artifact-path") == "portfolio/portfolio-sync.md"
    (landed,) = host.stub.find_calls("check-step-artifact")
    assert landed.value("--session-id") == step
    (postconditions,) = host.stub.find_calls("check-step-postconditions")
    assert postconditions.value("--session-id") == orchestrator
    assert opaque not in json.dumps([call.argv for call in host.stub.calls])
