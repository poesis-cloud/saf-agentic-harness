"""`claude-code` over the REAL harness: one governed step, end to end.

Core spec (Workspace Git plane; functions 0, 8, 9, 10): the step instance's writes land
on its own step ref — never a checkout — the written file stays in place in the working
tree, and only promotion moves the workspace branch. Payloads take the shapes captured on
Claude Code 2.1.323 (tests/functional/adapter_claude_code/fixtures/).
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

ADAPTER = "claude-code"
PLUGIN = "poesis-saf-agentic-organization"
SESSION = "cc5264f0-0ee1-4fcf-ab92-310faa224d3c"
WORKER_A = "abaaf063a6f522403"
WORKER_B = "a8a46e7597a677259"


@pytest.fixture
def real(tmp_path: Path) -> RealHarness:
    return build_real_harness(tmp_path, ADAPTER)


def _fire(real: RealHarness, event: str, payload: dict[str, Any]) -> dict | None:
    argv = [
        sys.executable,
        str(real.adapter_dir(ADAPTER) / "adapter.py"),
        "hook",
        "--event",
        event,
        "--framework-dir",
        str(real.framework_dir),
    ]
    body = {"session_id": SESSION, "hook_event_name": event, "cwd": str(real.workspace_dir),
            **payload}
    done = subprocess.run(
        argv,
        input=json.dumps(body).encode("utf-8"),
        capture_output=True,
        env=dict(real.environment),
        cwd=real.workspace_dir,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8")
    stdout = done.stdout.decode("utf-8")
    return json.loads(stdout) if stdout.strip() else None


def _specific(decision: dict | None) -> dict:
    return (decision or {}).get("hookSpecificOutput") or {}


def _denied(decision: dict | None) -> bool:
    return _specific(decision).get("permissionDecision") == "deny"


def _outcomes(real: RealHarness) -> list[dict[str, Any]]:
    return [
        json.loads(line)["report"]
        for log in (real.workspace_dir / "logs").glob("*.log.jsonl")
        for line in log.read_text(encoding="utf-8").splitlines()
        if '"check-step-postconditions"' in line
    ]


def _open_turn_and_resolve(real: RealHarness) -> dict[str, Any]:
    orchestrator = f"{PLUGIN}:{ORCHESTRATOR}"
    opened = _fire(real, "UserPromptSubmit", {"prompt_id": "p-1", "prompt": "go",
                                              "agent_type": orchestrator})
    assert "SAFe agentic harness" in _specific(opened)["additionalContext"]
    resolved = _fire(real, "PreToolUse", {"agent_type": orchestrator, "tool_name": "Bash",
                                          "tool_use_id": "toolu_r", "tool_input": {
        "command": f"python {real.harness_root / 'harness.py'} resolve-step --workflow-slug {WORKFLOW}",
        "description": "resolve"}})
    reason = _specific(resolved)["permissionDecisionReason"]
    assert reason.startswith("SAFe agentic harness RESULT (resolve-step)")
    report = json.loads(reason.split("\n\n", 1)[1])
    assert (report["outcome"]["status"], report["step"]["slug"]) == ("step-resolution", STEP)
    return report


def _dispatch(tool_use_id: str, background: bool | None = False) -> dict[str, Any]:
    tool_input: dict[str, Any] = {"subagent_type": f"{PLUGIN}:{ACTOR}", "description": "d",
                                  "prompt": "p"}
    if background is not None:
        tool_input["run_in_background"] = background
    return {"agent_type": f"{PLUGIN}:{ORCHESTRATOR}", "tool_name": "Agent",
            "tool_use_id": tool_use_id, "tool_input": tool_input}


def _returned(tool_use_id: str, worker: str) -> dict[str, Any]:
    dispatch = _dispatch(tool_use_id)
    return {**dispatch, "tool_response": {"status": "completed", "agentId": worker,
                                          "agentType": f"{PLUGIN}:{ACTOR}"}}


def _write(real: RealHarness, worker: str, tool: str = "Write") -> dict[str, Any]:
    artifact = str(real.workspace_dir / REPORT_REF)
    return {"agent_id": worker, "agent_type": f"{PLUGIN}:{ACTOR}", "tool_name": tool,
            "tool_use_id": f"toolu_w_{worker}",
            "tool_input": {"file_path": artifact, "content": REPORT_BYTES}}


def _stop(worker: str) -> dict[str, Any]:
    return {"agent_id": worker, "agent_type": f"{PLUGIN}:{ACTOR}", "stop_hook_active": False}


def test_a_governed_step_commits_on_its_step_ref_and_promotes_without_a_checkout(
    real: RealHarness,
) -> None:
    """Every boundary over the real harness: H0 from the conversation's `agent_type`,
    H1 correlated to its dispatch, H3/H5 attributed by `agent_id`, H6 promotes."""
    branch = real.git("symbolic-ref", "HEAD")
    head = real.git("rev-parse", "HEAD")
    report = _open_turn_and_resolve(real)

    assert not _denied(_fire(real, "PreToolUse", _dispatch("toolu_t1")))
    started = _fire(real, "SubagentStart", {"agent_id": WORKER_A,
                                            "agent_type": f"{PLUGIN}:{ACTOR}"})
    assert "SAFe agentic harness" in _specific(started)["additionalContext"]
    (step_ref,) = real.step_refs()
    assert step_ref.startswith(
        f"refs/harness/steps/{report['context']['workflowInstanceId']}/{STEP}/"
    )
    assert real.git("rev-parse", step_ref) == head

    assert not _denied(_fire(real, "PreToolUse", _write(real, WORKER_A)))
    assert real.lock_refs() == [f"refs/harness/locks/{REPORT_REF}"]
    real.write_report()
    assert _fire(real, "PostToolUse", {**_write(real, WORKER_A),
                                       "tool_response": {"type": "create"}}) is None
    assert real.git("symbolic-ref", "HEAD") == branch
    assert real.git("rev-parse", "HEAD") == head
    assert REPORT_REF in real.git("ls-tree", "-r", "--name-only", step_ref)

    _fire(real, "SubagentStop", _stop(WORKER_A))
    _fire(real, "PostToolUse", _returned("toolu_t1", WORKER_A))

    assert real.git("symbolic-ref", "HEAD") == branch
    assert real.git("rev-parse", "HEAD") == real.git("rev-parse", step_ref)
    assert real.git("rev-list", "--count", f"{head}..HEAD") == "1"
    assert real.lock_refs() == []
    assert [outcome["promotion"]["outcome"] for outcome in _outcomes(real)] == ["promoted"]

    _fire(real, "Stop", {"agent_type": f"{PLUGIN}:{ORCHESTRATOR}", "stop_hook_active": False})
    _fire(real, "SessionEnd", {"reason": "other"})


def test_a_dispatch_without_an_explicit_foreground_flag_is_denied(real: RealHarness) -> None:
    """Claude Code ran a dispatch that omitted `run_in_background` in the background
    (observed, 2.1.323): its return could never be evaluated, so it is denied."""
    _open_turn_and_resolve(real)
    for background in (None, True):
        decision = _fire(real, "PreToolUse", _dispatch("toolu_bg", background))
        assert _denied(decision)
        assert "run_in_background" in _specific(decision)["permissionDecisionReason"]
    assert not _denied(_fire(real, "PreToolUse", _dispatch("toolu_fg", False)))


def test_a_write_outside_a_step_and_a_shell_write_to_the_workspace_are_denied(
    real: RealHarness,
) -> None:
    """H3: the orchestrator's own write is denied (no step instance in flight); I9: a shell
    command naming the workspace is denied."""
    _open_turn_and_resolve(real)
    own = {**_write(real, "unused"), "agent_type": f"{PLUGIN}:{ORCHESTRATOR}"}
    del own["agent_id"]
    assert _denied(_fire(real, "PreToolUse", own))
    shell = _fire(real, "PreToolUse", {"agent_type": f"{PLUGIN}:{ORCHESTRATOR}",
                                       "tool_name": "Bash", "tool_use_id": "toolu_s",
                                       "tool_input": {"command": f"echo x > {real.workspace_dir / REPORT_REF}"}})
    assert _denied(shell)


def test_a_refused_duplicate_returning_first_never_concludes_the_bound_workers_step(
    real: RealHarness,
) -> None:
    """The orchestrator dispatches the resolved step twice. Worker B's registration is
    refused (`worker-session-bound`) and B returns FIRST: its return does not trigger
    `check-step-postconditions`; the bound worker A's return concludes and promotes."""
    head = real.git("rev-parse", "HEAD")
    _open_turn_and_resolve(real)
    assert not _denied(_fire(real, "PreToolUse", _dispatch("toolu_a")))
    assert not _denied(_fire(real, "PreToolUse", _dispatch("toolu_b")))
    _fire(real, "SubagentStart", {"agent_id": WORKER_A, "agent_type": f"{PLUGIN}:{ACTOR}"})
    _fire(real, "SubagentStart", {"agent_id": WORKER_B, "agent_type": f"{PLUGIN}:{ACTOR}"})
    (step_ref,) = real.step_refs()
    assert step_ref.endswith(f"/{STEP}/{WORKER_A}")
    assert _denied(_fire(real, "PreToolUse", _write(real, WORKER_B)))

    _fire(real, "SubagentStop", _stop(WORKER_B))
    returned_b = _fire(real, "PostToolUse", _returned("toolu_b", WORKER_B))
    assert "check-step-postconditions" not in json.dumps(returned_b or {})
    assert _outcomes(real) == []

    assert not _denied(_fire(real, "PreToolUse", _write(real, WORKER_A)))
    real.write_report()
    _fire(real, "PostToolUse", {**_write(real, WORKER_A), "tool_response": {"type": "create"}})
    _fire(real, "SubagentStop", _stop(WORKER_A))
    _fire(real, "PostToolUse", _returned("toolu_a", WORKER_A))

    (outcome,) = _outcomes(real)
    assert outcome["promotion"]["outcome"] == "promoted"
    assert real.git("rev-parse", "HEAD") == real.git("rev-parse", step_ref)
    assert real.git("rev-list", "--count", f"{head}..HEAD") == "1"


def test_an_unobserved_dispatch_leaves_its_subagent_ungoverned(real: RealHarness) -> None:
    """A subagent start that matches no dispatch the adapter let through is not attributed
    to any session: it stays ungoverned (no step ref), as with Copilot's `not-applicable`."""
    _open_turn_and_resolve(real)
    assert _fire(real, "SubagentStart", {"agent_id": WORKER_A,
                                         "agent_type": f"{PLUGIN}:{ACTOR}"}) is None
    assert real.step_refs() == []
