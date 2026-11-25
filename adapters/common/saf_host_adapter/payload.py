"""Seam 1: a host's stdin payload, read through its `tools.yaml` keys.

Two tool shapes are accepted: a BATCHED pre-tool firing (`toolCallsKeys`, observed in the
Copilot app: `{"toolCalls": [{"id", "name", "args"}, ...]}`) and the single-call shape
(`toolKeys` + `inputKeys`, every other observed firing). Anything else is malformed and the
gating boundary fails closed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .binding import ToolBinding

_UNSAFE_CHARACTER = re.compile(r"[^a-z0-9-]")


class MalformedPayloadError(ValueError):
    """Raised when a payload carries no tool call in either accepted shape."""


def sanitize_identifier(raw_identifier: str) -> str:
    """Normalize a host id to the safe slug form the harness context contract requires.

    Lowercase; any character outside `[a-z0-9-]` maps to `-` — the id becomes a log and a
    state filename, so a raw host id is a path-traversal vector.
    """
    return _UNSAFE_CHARACTER.sub("-", str(raw_identifier).lower())


@dataclass(frozen=True)
class ToolCall:
    """One tool call of a (possibly batched) firing."""

    name: str
    args: Any
    call_id: str | None = None


@dataclass(frozen=True)
class HookInput:
    """One host firing: the event name (from argv), its payload, and the scoping agent.

    `dispatcher_from_session`: whether the host session id names a subagent's dispatcher
    when the payload carries no parent id (true on hosts whose first-level subagent events
    carry the dispatcher as their session). When false, the dispatcher is not observed in
    the payload and the adapter correlates it from the dispatches it saw.
    """

    event: str
    payload: Mapping[str, Any]
    binding: ToolBinding
    scoping_agent: str | None = None
    dispatcher_from_session: bool = True
    _calls: tuple[ToolCall, ...] | None = field(default=None, repr=False, compare=False)

    @property
    def raw_host_session_id(self) -> str | None:
        value = self.binding.probe(self.payload, self.binding.host_session_keys)
        return str(value) if value is not None else None

    @property
    def host_id(self) -> str | None:
        """The sanitized host session id — inside a subagent it is the subagent's id."""
        raw = self.raw_host_session_id
        return sanitize_identifier(raw) if raw else None

    @property
    def root_host_id(self) -> str | None:
        """The sanitized id of the conversation every subagent of it shares, if the host
        names one apart from the session id; else the host session id itself."""
        value = self.binding.probe(self.payload, self.binding.host_root_session_keys)
        return sanitize_identifier(str(value)) if value is not None else self.host_id

    @property
    def turn_agent(self) -> str | None:
        """The agent selected for the conversation: the scoping registration's (`--agent`),
        else the host-observed one (`hostTurnAgentKeys`, its plugin namespace dropped)."""
        if self.scoping_agent:
            return self.scoping_agent
        value = self.binding.probe(self.payload, self.binding.host_turn_agent_keys)
        return self.binding.agent_name(value) if isinstance(value, str) and value else None

    @property
    def tool_use_id(self) -> str | None:
        value = self.binding.probe(self.payload, self.binding.host_tool_use_id_keys)
        return str(value) if value is not None else None

    @property
    def raw_parent_host_session_id(self) -> str | None:
        value = self.binding.probe(self.payload, self.binding.host_parent_session_keys)
        return str(value) if value is not None else None

    @property
    def parent_host_id(self) -> str | None:
        """The sanitized host id of the agent whose action opened this one, when observed."""
        raw = self.raw_parent_host_session_id
        return sanitize_identifier(raw) if raw else None

    @property
    def dispatcher_host_id(self) -> str | None:
        """At `subagentStart`/`subagentStop`: the host id of the agent that dispatched.

        The dispatcher is the host-observed `parentAgentId`. `sessionId` names it only for a
        first-level subagent; for a subagent dispatched by a subagent the runtime fills
        `sessionId` with an id no other event carries (observed, app 1.0.102).
        """
        if self.parent_host_id:
            return self.parent_host_id
        return self.host_id if self.dispatcher_from_session else None

    @property
    def timestamp(self) -> str | None:
        value = self.binding.probe(self.payload, self.binding.host_timestamp_keys)
        return str(value) if value is not None else None

    @property
    def cwd(self) -> str | None:
        value = self.binding.probe(self.payload, self.binding.host_cwd_keys)
        return str(value) if isinstance(value, str) else None

    def read_tool_calls(self) -> tuple[ToolCall, ...]:
        """Answer every tool call of this firing, in either accepted shape.

        Raises `MalformedPayloadError` when neither shape is present or an entry is
        not a named call — the caller fails closed at the gating boundary.
        """
        batch = self.binding.probe(self.payload, self.binding.tool_calls_keys)
        if batch is not None:
            if not isinstance(batch, list) or not batch:
                raise MalformedPayloadError("'toolCalls' is not a non-empty list")
            return tuple(self._read_batched(entry) for entry in batch)
        name = self.binding.probe(self.payload, self.binding.tool_keys)
        if not isinstance(name, str) or not name:
            raise MalformedPayloadError("payload names no tool (neither toolCalls nor toolName)")
        args = self.binding.probe(self.payload, self.binding.input_keys)
        return (ToolCall(name=name, args=_decode_args(args), call_id=self.tool_use_id),)

    def _read_batched(self, entry: Any) -> ToolCall:
        if not isinstance(entry, Mapping):
            raise MalformedPayloadError("a 'toolCalls' entry is not an object")
        name = self.binding.probe(entry, self.binding.tool_call_name_keys)
        if not isinstance(name, str) or not name:
            raise MalformedPayloadError("a 'toolCalls' entry names no tool")
        call_id = self.binding.probe(entry, self.binding.tool_call_id_keys)
        return ToolCall(
            name=name,
            args=_decode_args(self.binding.probe(entry, self.binding.tool_call_args_keys)),
            call_id=str(call_id) if call_id is not None else None,
        )


def _decode_args(args: Any) -> Any:
    """Decode string-encoded arguments when they are JSON; keep raw text otherwise.

    The runtime passes freeform tools' input (e.g. `apply_patch`) as a raw string.
    """
    if isinstance(args, str):
        stripped = args.strip()
        if stripped.startswith("{"):
            try:
                decoded = json.loads(stripped)
            except ValueError:
                return args
            return decoded if isinstance(decoded, Mapping) else args
    return args


__all__ = ["HookInput", "MalformedPayloadError", "ToolCall", "sanitize_identifier"]
