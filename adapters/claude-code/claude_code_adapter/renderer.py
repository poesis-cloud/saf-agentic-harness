"""Seam 4: the Claude Code hook stdout shapes, built from harness reports.

Claude Code reads ONE JSON object on a hook's stdout (exit 0). Every event-specific field
sits in `hookSpecificOutput`, whose `hookEventName` must name the firing event: the
decisions below leave it out and `render` fills it from the event. The adapter never
renders `permissionDecision: "allow"` (an allow would bypass the user's own permission
flow) nor `updatedInput`: it never edits an agent's command.

Claude Code stores an `additionalContext` over ~10,000 characters in a file and leaves only
a preview in the model's context (observed, 2.1.323), so every context is bounded by
`ADDITIONAL_CONTEXT_LIMIT`: H0/H1 fall back to load directives, and a post-tool context is
truncated.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from saf_host_adapter.reports import (
    ContextRenderer,
    describe_report,
    read_error,
    read_status,
    step_failed_text,
    write_rejected_text,
    write_unvalidated_text,
)

ADDITIONAL_CONTEXT_LIMIT = 10_000
_SPECIFIC = "hookSpecificOutput"
_TRUNCATED = "\n\n[truncated by the SAFe agentic harness adapter]"


def render(decision: Mapping[str, Any] | None, event: str) -> str:
    """Serialize a decision as the single stdout JSON object, or nothing (pass-through)."""
    if not decision:
        return ""
    rendered = dict(decision)
    specific = rendered.get(_SPECIFIC)
    if isinstance(specific, Mapping):
        rendered[_SPECIFIC] = {**specific, "hookEventName": event}
    return json.dumps(rendered, ensure_ascii=True, sort_keys=True)


def deny(reasons: Sequence[str]) -> dict[str, Any]:
    return {
        _SPECIFIC: {
            "permissionDecision": "deny",
            "permissionDecisionReason": "; ".join(reasons)
            or "denied by the SAFe agentic harness",
        }
    }


def additional_context(text: str) -> dict[str, Any]:
    return {_SPECIFIC: {"additionalContext": _truncate(text)}}


def fits_additional_context(text: str) -> bool:
    """Whether an `additionalContext` reaches the model whole, not as a file preview."""
    return len(text.encode("utf-8")) <= ADDITIONAL_CONTEXT_LIMIT


def context_of(decision: Mapping[str, Any]) -> str | None:
    specific = decision.get(_SPECIFIC)
    value = specific.get("additionalContext") if isinstance(specific, Mapping) else None
    return value if isinstance(value, str) and value else None


def blocked_result(reason: str, context: str) -> dict[str, Any]:
    """H5 — `decision: block` shows `reason` to the model next to the tool result (the
    write already ran: Claude Code cannot replace a built-in tool's result), and the
    retry directive rides in `additionalContext`."""
    return {
        "decision": "block",
        "reason": reason,
        _SPECIFIC: {"additionalContext": _truncate(context)},
    }


def write_rejected(report: Mapping[str, Any]) -> dict[str, Any]:
    return blocked_result(*write_rejected_text(report))


def write_unvalidated(report: Mapping[str, Any]) -> dict[str, Any]:
    return blocked_result(*write_unvalidated_text(report))


def step_failed(report: Mapping[str, Any]) -> dict[str, Any]:
    return additional_context(step_failed_text(report))


def _truncate(text: str) -> str:
    if len(text.encode("utf-8")) <= ADDITIONAL_CONTEXT_LIMIT:
        return text
    budget = ADDITIONAL_CONTEXT_LIMIT - len(_TRUNCATED.encode("utf-8"))
    return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + _TRUNCATED


class ClaudeCodeDecisions:
    """Claude Code's decision shapes, as the shared core asks for them.

    Every context Claude Code injects (`SessionStart`, `UserPromptSubmit`, `SubagentStart`,
    `PostToolUse`) is an `additionalContext` under the same cap.
    """

    deny = staticmethod(lambda reasons: deny(reasons))
    announce = staticmethod(lambda text: additional_context(text))
    step_context = staticmethod(lambda context: additional_context(context))
    fits_step_context = staticmethod(lambda context: fits_additional_context(context))
    settle_context = staticmethod(lambda text: additional_context(text))
    fits_settle_context = staticmethod(lambda text: fits_additional_context(text))
    context_of = staticmethod(lambda decision: context_of(decision))
    write_rejected = staticmethod(lambda report: write_rejected(report))
    write_unvalidated = staticmethod(lambda report: write_unvalidated(report))
    step_failed = staticmethod(lambda report: step_failed(report))

    @staticmethod
    def accepts_turn_context(hook: Any) -> bool:
        return True

    @staticmethod
    def turn_context(context: str, hook: Any) -> dict[str, Any]:
        return additional_context(context)

    @staticmethod
    def fits_turn_context(context: str, hook: Any) -> bool:
        return fits_additional_context(context)


__all__ = [
    "ADDITIONAL_CONTEXT_LIMIT",
    "ClaudeCodeDecisions",
    "ContextRenderer",
    "additional_context",
    "blocked_result",
    "context_of",
    "deny",
    "describe_report",
    "fits_additional_context",
    "read_error",
    "read_status",
    "render",
    "step_failed",
    "write_rejected",
    "write_unvalidated",
]
