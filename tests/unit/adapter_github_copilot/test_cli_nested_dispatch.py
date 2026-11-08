"""A step dispatched by a SUBAGENT: the parent is the host-observed `parentAgentId`.

Replays the app topology of a live trial (Windows, plugin agents): the ungoverned default
session dispatches the VMO orchestrator (`D1`), which dispatches a step acted by the VMO
again (`STEP`). For that nested `subagentStart` the runtime puts in `sessionId` an id no
other event carries (`OPAQUE`); only `parentAgentId` names the dispatcher.
"""

from __future__ import annotations

from typing import Any

from cli_rig import (
    CWD,
    HARNESS,
    PYTHON,
    WORKSPACE,
    Rig,
    build_error_report,
    build_report,
    post_tool_use,
    pre_tool_use,
)

PLUGIN = "poesis-saf-agentic-organization"
VMO = "value-management-officer"
ROOT = "95fd9c8d-1900-48cf-96c7-3c0a9d816674"
D1 = "d1fa24bb-97fd-4123-98d3-751ba40f3fa1"
STEP = "8e202f77-4865-4162-a74e-90d3273f72b6"
OPAQUE = "7abc93fb-b994-4ddc-bdf1-b567b3005b1d"
APP_CWD = r"C:\Users\cleme\.copilot\sandboxes\saf-org-trial"
ARTIFACT = WORKSPACE + r"\portfolio\portfolio-sync.md"

FIRST_LEVEL_START: dict[str, Any] = {
    "sessionId": ROOT,
    "transcriptPath": rf"C:\Users\cleme\.copilot\session-state\{ROOT}\events.jsonl",
    "agentId": D1,
    "agentType": f"{PLUGIN}:{VMO}",
    "agentName": f"{PLUGIN}:{VMO}",
    "agentDisplayName": VMO,
    "timestamp": 1793825800000,
    "parentAgentId": ROOT,
    "cwd": APP_CWD,
}
# Captured verbatim (description dropped).
NESTED_START: dict[str, Any] = {
    "sessionId": OPAQUE,
    "transcriptPath": rf"C:\Users\cleme\.copilot\session-state\{ROOT}\events.jsonl",
    "agentId": STEP,
    "agentType": f"{PLUGIN}:{VMO}",
    "agentName": f"{PLUGIN}:{VMO}",
    "agentDisplayName": VMO,
    "timestamp": 1793825897288,
    "parentAgentId": D1,
    "cwd": APP_CWD,
}


def _inside(agent_id: str, parent: str, **fields: Any) -> dict[str, Any]:
    """Any event fired inside a subagent: sessionId = agentId, plus parentAgentId."""
    return {
        "sessionId": agent_id,
        "agentId": agent_id,
        "agentType": f"{PLUGIN}:{VMO}",
        "parentAgentId": parent,
        "timestamp": 1793825900000,
        "cwd": APP_CWD,
        **fields,
    }


def _prompt(event: str, agent_id: str, parent: str) -> dict[str, Any]:
    payload = _inside(agent_id, parent, prompt="go")
    if event == "userPromptTransformed":
        payload["transformedPrompt"] = "<current_datetime>now</current_datetime> go"
    return payload


def _open_orchestrator(rig: Rig) -> str:
    """The default session (ungoverned) dispatches the VMO orchestrator; its H0 opens it."""
    assert rig.fire("subagentStart", FIRST_LEVEL_START) is None
    assert rig.runner.calls == []
    for event in ("userPromptSubmitted", "userPromptTransformed"):
        rig.fire(event, _prompt(event, D1, ROOT), VMO)
    record = rig.tracker.read(D1)
    assert record is not None and record.origin == "turn" and record.parent_session_id is None
    return record.session_id


def test_h1_opens_a_nested_step_under_the_parent_agent_not_the_session_id(rig: Rig) -> None:
    orchestrator = _open_orchestrator(rig)
    rig.runner.calls.clear()
    decision = rig.fire("subagentStart", NESTED_START)
    assert decision is not None and "additionalContext" in decision
    (inquiry,) = rig.runner.inquiries("start-session")
    assert inquiry == {"agent": VMO, "sessionId": STEP, "parentSessionId": orchestrator}
    record = rig.tracker.read(STEP)
    assert record.origin == "step" and record.parent_host_id == D1
    assert rig.tracker.read(OPAQUE) is None
    assert "resolve-step-instructions" in rig.runner.functions()
    assert "resolve-workflow-instructions" not in rig.runner.functions()


def test_the_scoped_h0_of_an_orchestrator_acting_a_step_leaves_the_step_session_alone(
    rig: Rig,
) -> None:
    _open_orchestrator(rig)
    rig.fire("subagentStart", NESTED_START)
    step = rig.tracker.read(STEP)
    rig.runner.calls.clear()
    for event in ("userPromptSubmitted", "userPromptTransformed"):
        assert rig.fire(event, _prompt(event, STEP, D1), VMO) is None
    assert rig.runner.calls == []
    assert rig.tracker.read(STEP) == step


def test_a_step_without_injected_context_still_gets_no_workflow_context_from_h0(
    rig: Rig,
) -> None:
    _open_orchestrator(rig)
    rig.runner.script["resolve-step-skills"] = RuntimeError("harness unavailable")
    rig.fire("subagentStart", NESTED_START)
    assert not rig.tracker.read(STEP).context_injected
    rig.runner.calls.clear()
    event = "userPromptTransformed"
    assert rig.fire(event, _prompt(event, STEP, D1), VMO) is None
    assert rig.runner.calls == []


def test_h0_never_opens_a_root_for_an_agent_a_governed_session_dispatched(rig: Rig) -> None:
    """H1 could not open the step: the actor stays ungoverned instead of becoming a root."""
    _open_orchestrator(rig)
    rig.runner.script["start-session"] = build_report("start-session", "not-applicable")
    assert rig.fire("subagentStart", NESTED_START) is None
    rig.runner.calls.clear()
    for event in ("userPromptSubmitted", "userPromptTransformed"):
        assert rig.fire(event, _prompt(event, STEP, D1), VMO) is None
    assert rig.runner.calls == []
    assert rig.tracker.read(STEP) is None


def test_the_nested_step_is_governed_end_to_end_under_its_own_session(rig: Rig) -> None:
    orchestrator = _open_orchestrator(rig)
    dispatch = pre_tool_use(("task", {"agent_type": f"{PLUGIN}:{VMO}", "prompt": "p"}), host=D1)
    assert rig.fire("preToolUse", dispatch) is None
    (pre,) = rig.runner.inquiries("check-step-preconditions")
    assert pre["sessionId"] == orchestrator
    rig.fire("subagentStart", NESTED_START)
    for event in ("userPromptSubmitted", "userPromptTransformed"):
        rig.fire(event, _prompt(event, STEP, D1), VMO)

    write = {"path": ARTIFACT, "file_text": "x"}
    assert rig.fire("preToolUse", {**pre_tool_use(("create", write), host=STEP),
                                   "agentId": STEP, "parentAgentId": D1}) is None
    (authorization,) = rig.runner.inquiries("check-step-authorization")
    assert authorization["sessionId"] == STEP
    rig.fire("postToolUse", {**post_tool_use("create", write, host=STEP), "parentAgentId": D1})
    (artifact,) = rig.runner.inquiries("check-step-artifact")
    assert (artifact["sessionId"], artifact["parentSessionId"]) == (STEP, orchestrator)

    rig.fire("agentStop", _inside(STEP, D1, stopReason="end_turn"))
    assert rig.runner.inquiries("end-session")[-1] == {"sessionId": STEP}
    rig.fire("subagentStop", {**NESTED_START, "stopReason": "end_turn", "response": "done"})
    rig.fire("postToolUse", post_tool_use("task", {"agent_type": f"{PLUGIN}:{VMO}"}, host=D1))
    (post,) = rig.runner.inquiries("check-step-postconditions")
    assert post["sessionId"] == orchestrator
    starts = rig.runner.inquiries("start-session")
    assert [inquiry["parentSessionId"] for inquiry in starts] == [None, orchestrator]


def test_session_end_closes_the_nested_step_under_the_root_orchestrator(rig: Rig) -> None:
    _open_orchestrator(rig)
    rig.fire("subagentStart", NESTED_START)
    rig.fire("sessionEnd", {"sessionId": D1, "reason": "user_exit", "cwd": CWD})
    assert rig.tracker.read(STEP) is None and rig.tracker.read(D1) is None


def test_a_nested_step_harness_request_is_attributed_with_its_tracked_parent(rig: Rig) -> None:
    """Trial: the actor's `resolve-step` (refused `not-facilitator`) was journaled with a
    null parent. The hook-executed H4 passes both host-observed ids: the worker session and
    the orchestrator session that opened it."""
    orchestrator = _open_orchestrator(rig)
    rig.fire("subagentStart", NESTED_START)
    rig.runner.calls.clear()
    rig.runner.script["resolve-step"] = build_error_report(
        "resolve-step", code="not-facilitator", message="Only the facilitator resolves steps."
    )
    command = f"& {PYTHON} {HARNESS} resolve-step --workflow-slug portfolio-sync"
    payload = {**pre_tool_use(("powershell", {"command": command}), host=STEP),
               "agentId": STEP, "parentAgentId": D1}
    decision = rig.fire("preToolUse", payload)
    assert decision is not None and decision["permissionDecision"] == "deny"
    assert "modifiedArgs" not in decision
    (inquiry,) = rig.runner.inquiries("resolve-step")
    assert inquiry == {
        "workflowSlug": "portfolio-sync",
        "sessionId": STEP,
        "parentSessionId": orchestrator,
    }
