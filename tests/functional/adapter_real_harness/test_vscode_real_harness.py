"""`vscode-github-copilot-chat` over the REAL harness: one governed step, end to end.

Core spec (Workspace Git plane; functions 0, 8, 9, 10): the step instance's writes land
on its own step ref — never a checkout — the written file stays in place in the working
tree, and only promotion moves the workspace branch.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from hook_dispatch import HookRun, run_hook
from host_events import (
    STEP_AGENT_ID,
    TURN_SESSION_ID,
    dispatch_tool_input,
    post_tool_use,
    pre_tool_use,
    subagent_start,
    subagent_stop,
    user_prompt_submit,
)
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

ADAPTER = "vscode-github-copilot-chat"


@pytest.fixture
def real(tmp_path: Path) -> RealHarness:
    return build_real_harness(tmp_path, ADAPTER)


def _fire(real: RealHarness, event: str, payload: Mapping[str, Any], agent: str | None = None) -> HookRun:
    run = run_hook(
        dispatch_path=real.harness_root / "adapters" / "dispatch.sh",
        event=event,
        payload=payload,
        environment=real.environment,
        agent=agent,
        cwd=real.framework_dir,
    )
    assert run.exit_code == 0, run.stderr
    return run


def _permission(run: HookRun) -> str | None:
    if run.is_silent:
        return None
    return run.decision().get("hookSpecificOutput", {}).get("permissionDecision")


def test_a_governed_step_commits_on_its_step_ref_and_promotes_without_a_checkout(
    real: RealHarness,
) -> None:
    """H0–H7 over the real harness: function 0 creates the step ref, function 8 claims
    the lock, function 9 commits onto the step ref with `HEAD` and the working tree
    unmoved, and function 10 promotes by one fast-forward — no checkout at any point."""
    branch = real.git("symbolic-ref", "HEAD")
    head = real.git("rev-parse", "HEAD")
    write_input = {"filePath": str(real.workspace_dir / REPORT_REF), "content": REPORT_BYTES}

    _fire(real, "UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR)
    resolved = _fire(
        real,
        "PreToolUse",
        pre_tool_use(
            "run_in_terminal",
            {"command": f"python harness.py resolve-step --workflow-slug {WORKFLOW}", "isBackground": False},
        ),
    )
    reason = resolved.decision()["hookSpecificOutput"]["permissionDecisionReason"]
    report = json.loads(reason.split("\n\n", 1)[1])
    assert (report["outcome"]["status"], report["step"]["slug"]) == ("step-resolution", STEP)

    assert _permission(_fire(real, "PreToolUse", pre_tool_use("runSubagent", dispatch_tool_input(ACTOR)))) != "deny"
    _fire(real, "SubagentStart", subagent_start(agent_type=ACTOR))
    (step_ref,) = real.step_refs()
    assert step_ref.endswith(f"/{STEP}/{STEP_AGENT_ID}")
    assert real.git("rev-parse", step_ref) == head

    assert _permission(_fire(real, "PreToolUse", pre_tool_use("create_file", write_input))) != "deny"
    assert real.lock_refs() == [f"refs/harness/locks/{REPORT_REF}"]
    real.write_report()
    _fire(real, "PostToolUse", post_tool_use("create_file", write_input))

    assert real.git("symbolic-ref", "HEAD") == branch
    assert real.git("rev-parse", "HEAD") == head
    assert (real.workspace_dir / REPORT_REF).read_bytes() == REPORT_BYTES.encode("utf-8")
    assert REPORT_REF in real.git("ls-tree", "-r", "--name-only", step_ref)
    assert real.git("diff", "--cached", "--name-only") == ""

    _fire(real, "SubagentStop", subagent_stop())
    ended = _fire(real, "PostToolUse", post_tool_use("runSubagent", dispatch_tool_input(ACTOR)))
    assert ended.is_silent or ended.decision().get("decision") != "block", ended.stdout

    assert real.git("symbolic-ref", "HEAD") == branch
    assert real.git("rev-parse", "HEAD") == real.git("rev-parse", step_ref)
    assert real.git("rev-list", "--count", f"{head}..HEAD") == "1"
    assert (real.workspace_dir / REPORT_REF).read_bytes() == REPORT_BYTES.encode("utf-8")
    assert real.git("status", "--porcelain", "--", "report") == ""
    assert real.lock_refs() == []
    journal = [
        json.loads(line)
        for line in (real.workspace_dir / "logs" / f"{TURN_SESSION_ID}.log.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    outcome = journal[-1]["report"]
    assert outcome["context"]["function"] == "check-step-postconditions"
    assert outcome["promotion"]["outcome"] == "promoted"


def _orchestrator_outcomes(real: RealHarness) -> list[dict[str, Any]]:
    log = real.workspace_dir / "logs" / f"{TURN_SESSION_ID}.log.jsonl"
    return [
        json.loads(line)["report"]
        for line in log.read_text(encoding="utf-8").splitlines()
        if '"check-step-postconditions"' in line
    ]


def test_a_refused_duplicate_returning_first_never_concludes_the_bound_workers_step(
    real: RealHarness,
) -> None:
    """Live trial T1: the orchestrator dispatches the resolved step twice; B returns FIRST.
    On this host the shared `session_id` stacks B under A, so function 0 never registers B
    as the step's worker (`not-applicable`). Adapter spec H6: a dispatch return whose
    sub-agent was never registered as the step's worker — or whose registration was
    refused — does not trigger `check-step-postconditions`: the step stays in flight for
    its bound worker A, whose write is allowed and whose return concludes and promotes
    the step."""
    head = real.git("rev-parse", "HEAD")
    write_input = {"filePath": str(real.workspace_dir / REPORT_REF), "content": REPORT_BYTES}
    _fire(real, "UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR)
    _fire(
        real,
        "PreToolUse",
        pre_tool_use(
            "run_in_terminal",
            {"command": f"python harness.py resolve-step --workflow-slug {WORKFLOW}", "isBackground": False},
        ),
    )
    for _ in range(2):
        assert _permission(_fire(real, "PreToolUse", pre_tool_use("runSubagent", dispatch_tool_input(ACTOR)))) != "deny"
    _fire(real, "SubagentStart", subagent_start(agent_id="worker-a", agent_type=ACTOR))
    _fire(real, "SubagentStart", subagent_start(agent_id="worker-b", agent_type=ACTOR))
    assert not (real.workspace_dir / "logs" / "worker-b.log.jsonl").exists()

    _fire(real, "SubagentStop", subagent_stop(agent_id="worker-b"))
    returned_b = _fire(real, "PostToolUse", post_tool_use("runSubagent", dispatch_tool_input(ACTOR)))

    assert returned_b.is_silent
    assert _orchestrator_outcomes(real) == []

    assert _permission(_fire(real, "PreToolUse", pre_tool_use("create_file", write_input))) != "deny"
    real.write_report()
    _fire(real, "PostToolUse", post_tool_use("create_file", write_input))
    (step_ref,) = real.step_refs()
    assert step_ref.endswith(f"/{STEP}/worker-a")
    assert REPORT_REF in real.git("ls-tree", "-r", "--name-only", step_ref)

    _fire(real, "SubagentStop", subagent_stop(agent_id="worker-a"))
    _fire(real, "PostToolUse", post_tool_use("runSubagent", dispatch_tool_input(ACTOR)))

    (outcome,) = _orchestrator_outcomes(real)
    assert outcome["outcome"]["status"] == "pass"
    assert outcome["promotion"]["outcome"] == "promoted"
    assert real.git("rev-parse", "HEAD") == real.git("rev-parse", step_ref)
    assert real.git("rev-list", "--count", f"{head}..HEAD") == "1"


def test_the_bound_worker_returning_before_the_duplicate_concludes_the_step_at_once(
    real: RealHarness,
) -> None:
    """Live trial T1, the other order: the bound worker A returns while the never-registered
    duplicate B still runs. Adapter spec H6: A's return concludes and promotes the step at
    once — only a STOPPED ignored sub-agent's return is skipped — and B's later return
    evaluates nothing."""
    write_input = {"filePath": str(real.workspace_dir / REPORT_REF), "content": REPORT_BYTES}
    _fire(real, "UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR)
    _fire(
        real,
        "PreToolUse",
        pre_tool_use(
            "run_in_terminal",
            {"command": f"python harness.py resolve-step --workflow-slug {WORKFLOW}", "isBackground": False},
        ),
    )
    _fire(real, "SubagentStart", subagent_start(agent_id="worker-a", agent_type=ACTOR))
    _fire(real, "SubagentStart", subagent_start(agent_id="worker-b", agent_type=ACTOR))
    assert _permission(_fire(real, "PreToolUse", pre_tool_use("create_file", write_input))) != "deny"
    real.write_report()
    _fire(real, "PostToolUse", post_tool_use("create_file", write_input))
    (step_ref,) = real.step_refs()

    _fire(real, "SubagentStop", subagent_stop(agent_id="worker-a"))
    _fire(real, "PostToolUse", post_tool_use("runSubagent", dispatch_tool_input(ACTOR)))

    (outcome,) = _orchestrator_outcomes(real)
    assert outcome["promotion"]["outcome"] == "promoted"
    assert real.git("rev-parse", "HEAD") == real.git("rev-parse", step_ref)

    _fire(real, "SubagentStop", subagent_stop(agent_id="worker-b"))
    _fire(real, "PostToolUse", post_tool_use("runSubagent", dispatch_tool_input(ACTOR)))

    assert len(_orchestrator_outcomes(real)) == 1
