"""Host event payloads for the seam-1 stdin contract, built as the host would write them.

Every builder produces a payload validated against
`adapters/vscode-github-copilot-chat/contracts/hook-stdin.schema.json` before a test can
feed it to `dispatch.sh`: a fixture the real host could never have written is caught here
rather than proving something false about the adapter downstream.
"""

from __future__ import annotations

from typing import Any, Mapping

from contract_assertions import assert_stdin_matches_contract

HOST_SESSION_ID = "chat-session-guid"
EVENT_TIMESTAMP = "2026-07-11T14:32:07.000Z"
# Adapter spec H0: `<sanitized session_id>-t<sanitized timestamp>`. Spelled out rather than
# recomputed with the adapter's own deriver — this suite is black-box at the process
# boundary, and an oracle that calls the code under test proves nothing.
TURN_SESSION_ID = "chat-session-guid-t2026-07-11t14-32-07-000z"
ORCHESTRATOR_AGENT = "value-management-officer"
STEP_AGENT_ID = "agent-run-01"
STEP_ACTOR = "qa-engineer"


def _envelope(event: str, session_id: str, **fields: Any) -> dict[str, Any]:
    payload = {
        "timestamp": EVENT_TIMESTAMP,
        "hook_event_name": event,
        "session_id": session_id,
        **fields,
    }
    assert_stdin_matches_contract(payload)
    return payload


def user_prompt_submit(session_id: str = HOST_SESSION_ID) -> dict[str, Any]:
    """H0 in — the agent-scoped session-started event; `prompt` is never read."""
    return _envelope("UserPromptSubmit", session_id, prompt="Advance the workflow.")


def subagent_start(
    agent_id: str = STEP_AGENT_ID,
    agent_type: str = STEP_ACTOR,
    session_id: str = HOST_SESSION_ID,
) -> dict[str, Any]:
    """H1 in — step-started: the subagent invocation id plus its framework actor."""
    return _envelope(
        "SubagentStart", session_id, agent_id=agent_id, agent_type=agent_type
    )


def pre_tool_use(
    tool_name: str,
    tool_input: Mapping[str, Any],
    session_id: str = HOST_SESSION_ID,
) -> dict[str, Any]:
    """H2/H3/H4 in — one `PreToolUse` firing, classified by `tool_name` (tools.yaml)."""
    return _envelope(
        "PreToolUse",
        session_id,
        tool_name=tool_name,
        tool_input=dict(tool_input),
        tool_use_id="call_abc123",
    )


def post_tool_use(
    tool_name: str,
    tool_input: Mapping[str, Any],
    session_id: str = HOST_SESSION_ID,
) -> dict[str, Any]:
    """H5/H6 in — one `PostToolUse` firing, classified by `tool_name` (tools.yaml)."""
    return _envelope(
        "PostToolUse",
        session_id,
        tool_name=tool_name,
        tool_input=dict(tool_input),
        tool_response="Created file.",
        tool_use_id="call_abc123",
    )


def subagent_stop(
    agent_id: str = STEP_AGENT_ID, session_id: str = HOST_SESSION_ID
) -> dict[str, Any]:
    """H7 in — the step session that just ended names itself through `agent_id`."""
    return _envelope(
        "SubagentStop", session_id, agent_id=agent_id, stop_hook_active=False
    )


def stop(session_id: str = HOST_SESSION_ID) -> dict[str, Any]:
    """H7 in — the turn ended; the ending session is resolved, never the raw id."""
    return _envelope("Stop", session_id, stop_hook_active=False)


def dispatch_tool_input(agent_name: str = STEP_ACTOR) -> dict[str, Any]:
    """The `runSubagent` call H2 gates and H6 evaluates."""
    return {
        "agentName": agent_name,
        "model": "Claude Sonnet 4.6 (copilot)",
        "prompt": "…the relayed step resolution…",
    }


__all__ = [
    "EVENT_TIMESTAMP",
    "HOST_SESSION_ID",
    "ORCHESTRATOR_AGENT",
    "STEP_ACTOR",
    "STEP_AGENT_ID",
    "TURN_SESSION_ID",
    "dispatch_tool_input",
    "post_tool_use",
    "pre_tool_use",
    "stop",
    "subagent_start",
    "subagent_stop",
    "user_prompt_submit",
]
