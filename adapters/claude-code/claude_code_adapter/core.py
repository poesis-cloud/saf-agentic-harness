"""The `claude-code` host profile over the shared adapter core.

Everything host-aware for the Claude Code runtime is this binding: the event names below,
the decision shapes of `renderer`, and the declarative `tools.yaml` / `models.yaml`. The
gating logic is the shared `saf_host_adapter.core` (adapter spec, Adapter architecture).

Boundary map (see `def/adapter/claude-code/spec.md`):

    H0  UserPromptSubmit (the conversation's `agent_type`)          start-session (turn)
    H1  SubagentStart (dispatcher correlated from its dispatch)     start-session (step)
    H2  PreToolUse · dispatch tool (`Agent`)                        check-step-preconditions
    H3  PreToolUse · write tool (`Write`/`Edit`/…)                  check-step-authorization
    H4  PreToolUse · `Bash` requesting `harness.py`                 resolve-step / resolve-step-model
                                                                    (run BY the hook; never by the shell)
    H5  PostToolUse · write tool                                    check-step-artifact
    H6  PostToolUse / PostToolUseFailure · dispatch tool            check-step-postconditions
    H7  Stop / SubagentStop / SessionEnd                            end-session
"""

from __future__ import annotations

from typing import Any

from saf_host_adapter import core as _shared
from saf_host_adapter.core import (
    HARNESS_REJECTED,
    HARNESS_RESULT,
    PLUGIN_UPDATED,
    HostEvents,
    HostProfile,
)

from .renderer import ClaudeCodeDecisions

ADAPTER_ID = "claude-code"

SESSION_START = "SessionStart"
USER_PROMPT_SUBMIT = "UserPromptSubmit"
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
POST_TOOL_USE_FAILURE = "PostToolUseFailure"
SUBAGENT_START = "SubagentStart"
SUBAGENT_STOP = "SubagentStop"
STOP = "Stop"
SESSION_END = "SessionEnd"
EVENTS = (
    SESSION_START,
    USER_PROMPT_SUBMIT,
    PRE_TOOL_USE,
    POST_TOOL_USE,
    POST_TOOL_USE_FAILURE,
    SUBAGENT_START,
    SUBAGENT_STOP,
    STOP,
    SESSION_END,
)
# Only `PreToolUse` can veto a tool call. Claude Code blocks on exit 2 at every event that
# supports blocking, and at `Stop`/`SubagentStop` that means "keep going": a failing hook
# there must never trap the agent, so only the gating event fails closed by exit code.
GATING_EVENT = PRE_TOOL_USE

CLAUDE_CODE_HOST = HostProfile(
    adapter_id=ADAPTER_ID,
    events=HostEvents(
        session_start=SESSION_START,
        turn_open=frozenset({USER_PROMPT_SUBMIT}),
        turn_context=USER_PROMPT_SUBMIT,
        subagent_start=SUBAGENT_START,
        pre_tool_use=PRE_TOOL_USE,
        post_tool_use=POST_TOOL_USE,
        post_tool_use_failure=POST_TOOL_USE_FAILURE,
        agent_stop=STOP,
        subagent_stop=SUBAGENT_STOP,
        session_end=SESSION_END,
    ),
    decisions=ClaudeCodeDecisions(),
    correlate_dispatches=True,
    # Another Stop/SubagentStop hook can veto a stop (`decision: block`): the agent keeps
    # working, so its session must stay governed until its end is certain.
    defer_stop_closure=True,
)


class Adapter(_shared.Adapter):
    """The shared adapter core, bound to the Claude Code runtime."""

    def __init__(self, *arguments: Any, **options: Any) -> None:
        super().__init__(CLAUDE_CODE_HOST, *arguments, **options)


__all__ = [
    "ADAPTER_ID",
    "Adapter",
    "CLAUDE_CODE_HOST",
    "EVENTS",
    "GATING_EVENT",
    "HARNESS_REJECTED",
    "HARNESS_RESULT",
    "PLUGIN_UPDATED",
]
