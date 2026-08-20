"""The two ends of one hook firing: the host event in, the host decision out."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

_ENVELOPE_SESSION_KEY = "session_id"
_ENVELOPE_TIMESTAMP_KEY = "timestamp"
_ENVELOPE_EVENT_KEY = "hook_event_name"
_TOOL_NAME_KEY = "tool_name"
_TOOL_INPUT_KEY = "tool_input"


@dataclass(frozen=True)
class HookEvent:
    """Carry one host firing, parsed from the seam-1 stdin contract.

    Spec (adapter, Invocation plumbing): seam 1 is `contracts/hook-stdin.schema.json` —
    the common envelope merged under every event's own fields. The scoping agent is NOT
    part of it: it arrives as `dispatch.sh`'s trailing argument (H0), host-fixed and
    never model-authored.
    """

    hook_event_name: str
    timestamp: str
    raw_host_session_id: str
    tool_name: str | None = None
    tool_input: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict)
    scoping_agent: str | None = None

    @classmethod
    def build_from_payload(
        cls, payload: Mapping[str, Any], scoping_agent: str | None = None
    ) -> "HookEvent":
        """Build one event from the host's stdin payload plus the dispatch argument."""
        tool_input = payload.get(_TOOL_INPUT_KEY)
        tool_name = payload.get(_TOOL_NAME_KEY)
        return cls(
            hook_event_name=str(payload.get(_ENVELOPE_EVENT_KEY, "")),
            timestamp=str(payload.get(_ENVELOPE_TIMESTAMP_KEY, "")),
            raw_host_session_id=str(payload.get(_ENVELOPE_SESSION_KEY, "")),
            tool_name=str(tool_name) if tool_name is not None else None,
            tool_input=dict(tool_input) if isinstance(tool_input, Mapping) else {},
            payload=dict(payload),
            scoping_agent=scoping_agent,
        )


@dataclass(frozen=True)
class HookDecision:
    """Carry the answer to the host: an exit code plus the seam-4 stdout.

    Spec (adapter, I5): the canonical control surface is STRUCTURED STDOUT JSON ON EXIT 0;
    exit 2 remains only the hard-failure fallback. Empty stdout on exit 0 is the valid
    pass-through and is deliberately not governed by the stdout contract.
    """

    exit_code: int
    stdout: str


__all__ = ["HookDecision", "HookEvent"]
