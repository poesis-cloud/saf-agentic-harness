"""Seam 4: the Copilot CLI hook stdout shapes, built from harness reports.

Every rendered object is ONE JSON line, ASCII-escaped so a Windows console code page can
never corrupt it. The adapter never renders `permissionDecision: "allow"` — an allow
would bypass the user's own permission flow; an authorized call simply passes through. Nor
does it ever render `modifiedArgs`: the adapter never edits an agent's command.
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

_CONTEXT_SEPARATOR = "\n\n---\n\n"
# postToolUse `additionalContext` is capped by the runtime at 10 KB (hooks reference).
ADDITIONAL_CONTEXT_LIMIT = 10_000
# Any hook's stdout is bounded at 10 MiB; it is the only bound on `subagentStart` /
# `sessionStart` `additionalContext` and on `modifiedTransformedPrompt` (hooks reference).
HOOK_OUTPUT_LIMIT = 10 * 1024 * 1024


def render(decision: Mapping[str, Any] | None) -> str:
    """Serialize a decision as the single stdout JSON object, or nothing (pass-through)."""
    if not decision:
        return ""
    return json.dumps(decision, ensure_ascii=True, sort_keys=True)


def deny(reasons: Sequence[str]) -> dict[str, Any]:
    return {
        "permissionDecision": "deny",
        "permissionDecisionReason": "; ".join(reasons) or "denied by the SAFe agentic harness",
    }


def additional_context(text: str) -> dict[str, Any]:
    return {"additionalContext": _truncate(text)}


def fits_hook_output(decision: Mapping[str, Any]) -> bool:
    """Whether the rendered stdout line stays inside the runtime's hook-output limit."""
    return len(render(decision).encode("utf-8")) + 1 <= HOOK_OUTPUT_LIMIT


def fits_additional_context(text: str) -> bool:
    """Whether a postToolUse `additionalContext` is delivered whole (not truncated)."""
    return len(text.encode("utf-8")) <= ADDITIONAL_CONTEXT_LIMIT


def step_context(text: str) -> dict[str, Any]:
    """H1 — `subagentStart` context: bounded by the hook-output limit only, never cut."""
    return {"additionalContext": text}


def transformed_prompt(context: str, transformed: str) -> dict[str, Any]:
    return {"modifiedTransformedPrompt": f"{context}{_CONTEXT_SEPARATOR}{transformed}"}


def write_rejected(report: Mapping[str, Any]) -> dict[str, Any]:
    """H5 — overwrite the tool result the model sees, and direct the retry."""
    result, context = write_rejected_text(report)
    return {
        "modifiedResult": {"resultType": "success", "textResultForLlm": result},
        "additionalContext": _truncate(context),
    }


def write_unvalidated(report: Mapping[str, Any]) -> dict[str, Any]:
    """H5 — an error outcome: the write was not validated; never claim it was restored."""
    result, context = write_unvalidated_text(report)
    return {
        "modifiedResult": {"resultType": "success", "textResultForLlm": result},
        "additionalContext": _truncate(context),
    }


def step_failed(report: Mapping[str, Any]) -> dict[str, Any]:
    """H6 — relay the failed postconditions with the re-resolve directive."""
    return additional_context(step_failed_text(report))


def context_of(decision: Mapping[str, Any]) -> str | None:
    """The `additionalContext` a decision already carries, if any."""
    value = decision.get("additionalContext")
    return value if isinstance(value, str) and value else None


def _truncate(text: str) -> str:
    if len(text.encode("utf-8")) <= ADDITIONAL_CONTEXT_LIMIT:
        return text
    marker = "\n\n[truncated by the SAFe agentic harness adapter]"
    budget = ADDITIONAL_CONTEXT_LIMIT - len(marker.encode("utf-8"))
    return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + marker




class CopilotDecisions:
    """The Copilot runtime's decision shapes, as the shared core asks for them.

    Every method reads this module's functions (and limits) at call time.
    """

    deny = staticmethod(lambda reasons: deny(reasons))
    announce = staticmethod(lambda text: additional_context(text))
    step_context = staticmethod(lambda context: step_context(context))
    settle_context = staticmethod(lambda text: additional_context(text))
    fits_settle_context = staticmethod(lambda text: fits_additional_context(text))
    context_of = staticmethod(lambda decision: context_of(decision))
    write_rejected = staticmethod(lambda report: write_rejected(report))
    write_unvalidated = staticmethod(lambda report: write_unvalidated(report))
    step_failed = staticmethod(lambda report: step_failed(report))

    @staticmethod
    def fits_step_context(context: str) -> bool:
        return fits_hook_output(step_context(context))

    @staticmethod
    def accepts_turn_context(hook: Any) -> bool:
        """H0 injects only into the runtime's transformed prompt (`userPromptTransformed`)."""
        transformed = hook.payload.get("transformedPrompt")
        return isinstance(transformed, str) and bool(transformed)

    @staticmethod
    def turn_context(context: str, hook: Any) -> dict[str, Any]:
        return transformed_prompt(context, str(hook.payload.get("transformedPrompt")))

    @staticmethod
    def fits_turn_context(context: str, hook: Any) -> bool:
        return fits_hook_output(
            transformed_prompt(context, str(hook.payload.get("transformedPrompt")))
        )


__all__ = [
    "ADDITIONAL_CONTEXT_LIMIT",
    "CopilotDecisions",
    "HOOK_OUTPUT_LIMIT",
    "ContextRenderer",
    "additional_context",
    "context_of",
    "deny",
    "describe_report",
    "fits_additional_context",
    "fits_hook_output",
    "read_error",
    "read_status",
    "render",
    "step_context",
    "step_failed",
    "transformed_prompt",
    "write_rejected",
    "write_unvalidated",
]
