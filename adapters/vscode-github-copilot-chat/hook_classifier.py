"""Host event plus tool class into exactly one `EventClass`."""

from __future__ import annotations

from event_class import EventClass
from hook_binding import HookBinding
from hook_event import HookEvent

_USER_PROMPT_SUBMIT = "UserPromptSubmit"
_SUBAGENT_START = "SubagentStart"
_PRE_TOOL_USE = "PreToolUse"
_POST_TOOL_USE = "PostToolUse"
_SESSION_ENDING_EVENTS = frozenset({"SubagentStop", "Stop"})


class HookClassifier:
    """Classify one firing into the boundary it binds to.

    Spec (adapter, Boundary binding): the host has NO per-tool matcher — one
    `PreToolUse` / `PostToolUse` registration fires for every tool call, and tool
    discrimination is the adapter's job, driven by its own `tools.yaml`. Any firing
    matching no declared class is a pass-through: exit 0, empty stdout, no journal entry
    (C7).
    """

    def __init__(self, binding: HookBinding) -> None:
        """Create the classifier over this adapter's own binding data."""
        self._binding = binding

    def classify_event(self, event: HookEvent) -> EventClass:
        """Classify one host firing."""
        if event.hook_event_name == _USER_PROMPT_SUBMIT:
            return EventClass.SESSION_STARTED
        if event.hook_event_name == _SUBAGENT_START:
            return EventClass.STEP_STARTED
        if event.hook_event_name in _SESSION_ENDING_EVENTS:
            return EventClass.SESSION_ENDED
        if event.hook_event_name == _PRE_TOOL_USE:
            return self._classify_pre_tool_use(event.tool_name)
        if event.hook_event_name == _POST_TOOL_USE:
            return self._classify_post_tool_use(event.tool_name)
        return EventClass.PASS_THROUGH

    def _classify_pre_tool_use(self, tool_name: str | None) -> EventClass:
        if self._binding.is_dispatch_tool(tool_name):
            return EventClass.STEP_STARTING
        if self._binding.is_write_class_tool(tool_name):
            return EventClass.WRITE_STARTING
        if self._binding.is_mediated_command_tool(
            tool_name
        ) or self._binding.is_guarded_shell_tool(tool_name):
            return EventClass.MEDIATED_ATTRIBUTION
        return EventClass.PASS_THROUGH

    def _classify_post_tool_use(self, tool_name: str | None) -> EventClass:
        if self._binding.is_dispatch_tool(tool_name):
            return EventClass.STEP_ENDED
        if self._binding.is_write_class_tool(tool_name):
            return EventClass.WRITE_ENDED
        return EventClass.PASS_THROUGH


__all__ = ["HookClassifier"]
