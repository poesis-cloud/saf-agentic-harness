"""The `github-copilot` host profile over the shared adapter core.

Everything host-aware for the GitHub Copilot agent runtime (the Copilot CLI, the Copilot app
and VS Code with Session Target: Copilot) is this binding: the event names below, the decision
shapes of `renderer`, and the declarative `tools.yaml` / `models.yaml`. The gating logic is
the shared `saf_host_adapter.core` (adapter spec, Adapter architecture).

Boundary map (see `def/adapter/github-copilot/spec.md`):

    H0  userPromptSubmitted / userPromptTransformed (agent-scoped)  start-session (turn)
    H1  subagentStart                                               start-session (step)
    H2  preToolUse · dispatch tool (`task`)                         check-step-preconditions
    H3  preToolUse · write tool                                     check-step-authorization
    H4  preToolUse · shell tool requesting `harness.py`             resolve-step / resolve-step-model
                                                                    (run BY the hook; never by the shell)
    H5  postToolUse · write tool                                    check-step-artifact
    H6  postToolUse / postToolUseFailure · dispatch tool            check-step-postconditions
    H7  agentStop / subagentStop / sessionEnd                       end-session
"""

from __future__ import annotations

from typing import Any

from saf_host_adapter import core as _shared
from saf_host_adapter.core import (
    HARNESS_REJECTED,
    HARNESS_RESULT,
    PLUGIN_UPDATED,
    HarnessRequest,
    HostEvents,
    HostProfile,
    classify_calls,
)

from .renderer import CopilotDecisions

ADAPTER_ID = "github-copilot"

SESSION_START = "sessionStart"
USER_PROMPT_SUBMITTED = "userPromptSubmitted"
USER_PROMPT_TRANSFORMED = "userPromptTransformed"
PRE_TOOL_USE = "preToolUse"
POST_TOOL_USE = "postToolUse"
POST_TOOL_USE_FAILURE = "postToolUseFailure"
SUBAGENT_START = "subagentStart"
SUBAGENT_STOP = "subagentStop"
AGENT_STOP = "agentStop"
SESSION_END = "sessionEnd"
ERROR_OCCURRED = "errorOccurred"
EVENTS = (
    SESSION_START,
    USER_PROMPT_SUBMITTED,
    USER_PROMPT_TRANSFORMED,
    PRE_TOOL_USE,
    POST_TOOL_USE,
    POST_TOOL_USE_FAILURE,
    SUBAGENT_START,
    SUBAGENT_STOP,
    AGENT_STOP,
    SESSION_END,
    ERROR_OCCURRED,
)

COPILOT_HOST = HostProfile(
    adapter_id=ADAPTER_ID,
    events=HostEvents(
        session_start=SESSION_START,
        turn_open=frozenset({USER_PROMPT_SUBMITTED, USER_PROMPT_TRANSFORMED}),
        turn_context=USER_PROMPT_TRANSFORMED,
        subagent_start=SUBAGENT_START,
        pre_tool_use=PRE_TOOL_USE,
        post_tool_use=POST_TOOL_USE,
        post_tool_use_failure=POST_TOOL_USE_FAILURE,
        agent_stop=AGENT_STOP,
        subagent_stop=SUBAGENT_STOP,
        session_end=SESSION_END,
        error_occurred=ERROR_OCCURRED,
    ),
    decisions=CopilotDecisions(),
)


class Adapter(_shared.Adapter):
    """The shared adapter core, bound to the Copilot runtime."""

    def __init__(self, *arguments: Any, **options: Any) -> None:
        super().__init__(COPILOT_HOST, *arguments, **options)


__all__ = [
    "ADAPTER_ID",
    "Adapter",
    "COPILOT_HOST",
    "EVENTS",
    "HARNESS_REJECTED",
    "HARNESS_RESULT",
    "PLUGIN_UPDATED",
    "HarnessRequest",
    "classify_calls",
]
