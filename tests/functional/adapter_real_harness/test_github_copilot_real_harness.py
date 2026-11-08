"""`github-copilot` over the REAL harness: one governed step, end to end.

Core spec (Workspace Git plane; functions 0, 8, 9, 10): the step instance's writes land
on its own step ref — never a checkout — the written file stays in place in the working
tree, and only promotion moves the workspace branch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from real_harness_rig import (
    ACTOR,
    ORCHESTRATOR,
    REPORT_BYTES,
    REPORT_REF,
    STEP,
    WORKFLOW,
    RealHarness,
    build_real_harness,
)

ADAPTER = "github-copilot"
HOST = "1d793d07-adc6-4f25-bec3-5b03b44e949e"
SUBAGENT = "ecdda9f8-7300-43cc-9fae-ad08c0456604"


@pytest.fixture
def real(tmp_path: Path) -> RealHarness:
    return build_real_harness(tmp_path, ADAPTER)


def _fire(real: RealHarness, event: str, payload: Any, agent: str | None = None) -> dict | None:
    argv = [
        sys.executable,
        str(real.adapter_dir(ADAPTER) / "adapter.py"),
        "hook",
        "--event",
        event,
        "--framework-dir",
        str(real.framework_dir),
    ] + (["--agent", agent] if agent else [])
    done = subprocess.run(
        argv,
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=dict(real.environment),
        cwd=real.framework_dir,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8")
    stdout = done.stdout.decode("utf-8")
    return json.loads(stdout) if stdout.strip() else None


def _denied(decision: dict | None) -> bool:
    return decision is not None and decision.get("permissionDecision") == "deny"


def test_a_governed_step_commits_on_its_step_ref_and_promotes_without_a_checkout(
    real: RealHarness,
) -> None:
    """Every boundary over the real harness: function 0 creates the step ref, function 8
    claims the lock, function 9 commits onto the step ref with `HEAD` and the working
    tree unmoved, and function 10 promotes by one fast-forward — no checkout at all."""
    branch = real.git("symbolic-ref", "HEAD")
    head = real.git("rev-parse", "HEAD")
    artifact = str(real.workspace_dir / REPORT_REF)
    cwd = str(real.workspace_dir)
    shell = "powershell" if sys.platform == "win32" else "bash"

    _fire(real, "userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1, "cwd": cwd}, ORCHESTRATOR)
    resolved = _fire(real, "preToolUse", {"sessionId": HOST, "cwd": cwd, "toolCalls": [
        {"id": "r", "name": shell, "args": {
            "command": f"python {real.harness_root / 'harness.py'} resolve-step --workflow-slug {WORKFLOW}",
            "description": "resolve"}}]})
    report = json.loads(resolved["permissionDecisionReason"].split("\n\n", 1)[1])
    assert (report["outcome"]["status"], report["step"]["slug"]) == ("step-resolution", STEP)

    assert not _denied(_fire(real, "preToolUse", {"sessionId": HOST, "cwd": cwd, "toolCalls": [
        {"id": "t1", "name": "task", "args": {"agent_type": ACTOR, "prompt": "p"}}]}))
    _fire(real, "subagentStart", {"sessionId": HOST, "agentId": SUBAGENT, "agentType": ACTOR, "timestamp": 2})
    (step_ref,) = real.step_refs()
    assert step_ref.startswith(f"refs/harness/steps/{report['context']['workflowInstanceId']}/{STEP}/")
    assert real.git("rev-parse", step_ref) == head

    write = {"sessionId": SUBAGENT, "agentId": SUBAGENT, "agentType": ACTOR, "cwd": cwd, "toolCalls": [
        {"id": "t2", "name": "create", "args": {"path": artifact, "file_text": REPORT_BYTES}}]}
    assert not _denied(_fire(real, "preToolUse", write))
    assert real.lock_refs() == [f"refs/harness/locks/{REPORT_REF}"]
    real.write_report()
    _fire(real, "postToolUse", {"sessionId": SUBAGENT, "timestamp": 3, "toolName": "create",
                                "toolArgs": {"path": artifact, "file_text": REPORT_BYTES},
                                "toolResult": {"resultType": "success", "textResultForLlm": "ok"}})

    assert real.git("symbolic-ref", "HEAD") == branch
    assert real.git("rev-parse", "HEAD") == head
    assert (real.workspace_dir / REPORT_REF).read_bytes() == REPORT_BYTES.encode("utf-8")
    assert REPORT_REF in real.git("ls-tree", "-r", "--name-only", step_ref)
    assert real.git("diff", "--cached", "--name-only") == ""

    _fire(real, "subagentStop", {"sessionId": HOST, "agentId": SUBAGENT, "agentType": ACTOR,
                                 "stopReason": "end_turn", "timestamp": 4})
    _fire(real, "postToolUse", {"sessionId": HOST, "timestamp": 5, "toolName": "task",
                                "toolArgs": {"agent_type": ACTOR, "prompt": "p"},
                                "toolResult": {"resultType": "success", "textResultForLlm": "done"}})

    assert real.git("symbolic-ref", "HEAD") == branch
    assert real.git("rev-parse", "HEAD") == real.git("rev-parse", step_ref)
    assert real.git("rev-list", "--count", f"{head}..HEAD") == "1"
    assert (real.workspace_dir / REPORT_REF).read_bytes() == REPORT_BYTES.encode("utf-8")
    assert real.git("status", "--porcelain", "--", "report") == ""
    assert real.lock_refs() == []
    outcomes = [
        json.loads(line)["report"]
        for log in (real.workspace_dir / "logs").glob("*.log.jsonl")
        for line in log.read_text(encoding="utf-8").splitlines()
        if '"check-step-postconditions"' in line
    ]
    assert [outcome["promotion"]["outcome"] for outcome in outcomes] == ["promoted"]


def _outcomes(real: RealHarness) -> list[dict[str, Any]]:
    return [
        json.loads(line)["report"]
        for log in (real.workspace_dir / "logs").glob("*.log.jsonl")
        for line in log.read_text(encoding="utf-8").splitlines()
        if '"check-step-postconditions"' in line
    ]


def test_a_refused_duplicate_returning_first_never_concludes_the_bound_workers_step(
    real: RealHarness,
) -> None:
    """Live trial T1: the orchestrator dispatches the resolved step twice. Worker B's
    registration is refused (`worker-session-bound`) and B returns FIRST. Adapter spec H6:
    a dispatch return whose sub-agent's registration was refused does not trigger
    `check-step-postconditions` — the step stays in flight for its bound worker A, whose
    write is allowed and whose return concludes and promotes the step."""
    head = real.git("rev-parse", "HEAD")
    artifact = str(real.workspace_dir / REPORT_REF)
    cwd = str(real.workspace_dir)
    shell = "powershell" if sys.platform == "win32" else "bash"
    worker_a, worker_b = "aaaaaaaa-0000-4000-8000-00000000000a", "bbbbbbbb-0000-4000-8000-00000000000b"
    task = {"agent_type": ACTOR, "prompt": "p"}

    _fire(real, "userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1, "cwd": cwd}, ORCHESTRATOR)
    _fire(real, "preToolUse", {"sessionId": HOST, "cwd": cwd, "toolCalls": [
        {"id": "r", "name": shell, "args": {
            "command": f"python {real.harness_root / 'harness.py'} resolve-step --workflow-slug {WORKFLOW}",
            "description": "resolve"}}]})
    assert not _denied(_fire(real, "preToolUse", {"sessionId": HOST, "cwd": cwd, "toolCalls": [
        {"id": "t1", "name": "task", "args": task}, {"id": "t2", "name": "task", "args": task}]}))
    _fire(real, "subagentStart", {"sessionId": HOST, "agentId": worker_a, "agentType": ACTOR, "timestamp": 2})
    _fire(real, "subagentStart", {"sessionId": HOST, "agentId": worker_b, "agentType": ACTOR, "timestamp": 3})
    (step_ref,) = real.step_refs()
    assert step_ref.endswith(f"/{STEP}/{worker_a}")

    _fire(real, "agentStop", {"sessionId": worker_b, "stopReason": "end_turn", "timestamp": 4})
    _fire(real, "subagentStop", {"sessionId": HOST, "agentId": worker_b, "agentType": ACTOR,
                                 "stopReason": "end_turn", "timestamp": 5})
    returned_b = _fire(real, "postToolUse", {"sessionId": HOST, "timestamp": 6, "toolName": "task",
                                             "toolArgs": task,
                                             "toolResult": {"resultType": "success", "textResultForLlm": "b"}})

    assert "check-step-postconditions" not in json.dumps(returned_b or {})
    assert _outcomes(real) == []

    write = {"sessionId": worker_a, "agentId": worker_a, "agentType": ACTOR, "cwd": cwd, "toolCalls": [
        {"id": "w", "name": "create", "args": {"path": artifact, "file_text": REPORT_BYTES}}]}
    assert not _denied(_fire(real, "preToolUse", write))
    real.write_report()
    _fire(real, "postToolUse", {"sessionId": worker_a, "timestamp": 7, "toolName": "create",
                                "toolArgs": {"path": artifact, "file_text": REPORT_BYTES},
                                "toolResult": {"resultType": "success", "textResultForLlm": "ok"}})
    assert REPORT_REF in real.git("ls-tree", "-r", "--name-only", step_ref)

    _fire(real, "agentStop", {"sessionId": worker_a, "stopReason": "end_turn", "timestamp": 8})
    _fire(real, "subagentStop", {"sessionId": HOST, "agentId": worker_a, "agentType": ACTOR,
                                 "stopReason": "end_turn", "timestamp": 9})
    _fire(real, "postToolUse", {"sessionId": HOST, "timestamp": 10, "toolName": "task",
                                "toolArgs": task,
                                "toolResult": {"resultType": "success", "textResultForLlm": "a"}})

    (outcome,) = _outcomes(real)
    assert outcome["outcome"]["status"] == "pass"
    assert outcome["promotion"]["outcome"] == "promoted"
    assert real.git("rev-parse", "HEAD") == real.git("rev-parse", step_ref)
    assert real.git("rev-list", "--count", f"{head}..HEAD") == "1"


def test_the_bound_worker_returning_before_the_refused_one_concludes_the_step_at_once(
    real: RealHarness,
) -> None:
    """Live trial T1, the other order: the bound worker A returns while the refused
    duplicate B still runs. Adapter spec H6: A's return concludes and promotes the step at
    once — only a STOPPED ignored sub-agent's return is skipped — and B's later return
    evaluates nothing."""
    artifact = str(real.workspace_dir / REPORT_REF)
    cwd = str(real.workspace_dir)
    shell = "powershell" if sys.platform == "win32" else "bash"
    worker_a, worker_b = "aaaaaaaa-0000-4000-8000-00000000000a", "bbbbbbbb-0000-4000-8000-00000000000b"
    task = {"agent_type": ACTOR, "prompt": "p"}

    _fire(real, "userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1, "cwd": cwd}, ORCHESTRATOR)
    _fire(real, "preToolUse", {"sessionId": HOST, "cwd": cwd, "toolCalls": [
        {"id": "r", "name": shell, "args": {
            "command": f"python {real.harness_root / 'harness.py'} resolve-step --workflow-slug {WORKFLOW}",
            "description": "resolve"}}]})
    _fire(real, "subagentStart", {"sessionId": HOST, "agentId": worker_a, "agentType": ACTOR, "timestamp": 2})
    _fire(real, "subagentStart", {"sessionId": HOST, "agentId": worker_b, "agentType": ACTOR, "timestamp": 3})
    write = {"sessionId": worker_a, "agentId": worker_a, "agentType": ACTOR, "cwd": cwd, "toolCalls": [
        {"id": "w", "name": "create", "args": {"path": artifact, "file_text": REPORT_BYTES}}]}
    assert not _denied(_fire(real, "preToolUse", write))
    real.write_report()
    _fire(real, "postToolUse", {"sessionId": worker_a, "timestamp": 4, "toolName": "create",
                                "toolArgs": {"path": artifact, "file_text": REPORT_BYTES},
                                "toolResult": {"resultType": "success", "textResultForLlm": "ok"}})
    (step_ref,) = real.step_refs()

    _fire(real, "agentStop", {"sessionId": worker_a, "stopReason": "end_turn", "timestamp": 5})
    _fire(real, "subagentStop", {"sessionId": HOST, "agentId": worker_a, "agentType": ACTOR,
                                 "stopReason": "end_turn", "timestamp": 6})
    _fire(real, "postToolUse", {"sessionId": HOST, "timestamp": 7, "toolName": "task", "toolArgs": task,
                                "toolResult": {"resultType": "success", "textResultForLlm": "a"}})

    (outcome,) = _outcomes(real)
    assert outcome["promotion"]["outcome"] == "promoted"
    assert real.git("rev-parse", "HEAD") == real.git("rev-parse", step_ref)

    _fire(real, "agentStop", {"sessionId": worker_b, "stopReason": "end_turn", "timestamp": 8})
    _fire(real, "subagentStop", {"sessionId": HOST, "agentId": worker_b, "agentType": ACTOR,
                                 "stopReason": "end_turn", "timestamp": 9})
    _fire(real, "postToolUse", {"sessionId": HOST, "timestamp": 10, "toolName": "task", "toolArgs": task,
                                "toolResult": {"resultType": "success", "textResultForLlm": "b"}})

    assert len(_outcomes(real)) == 1
