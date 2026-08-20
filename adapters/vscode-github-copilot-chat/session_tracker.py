"""The adapter's own private record of which agent session a host session is in."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, MutableMapping


class SessionTracker:
    """Keep a STACK of agent sessions per raw host session id.

    Spec (adapter, Session identity binding): a subagent's own `session_id` is the SAME
    shared conversation id as its dispatching session's (I13(b)), so a flat pointer
    cannot resolve nesting. H0 resets the stack fresh per turn, H1 pushes the new step
    session, H7 pops it on `SubagentStop` and clears the whole stack on `Stop`; every
    other hook only reads. This is the adapter's own small persistent record — plain file
    I/O, never the harness's `SessionLogStore`, which the adapter cannot read at all.
    """

    def __init__(self, store_path: Path) -> None:
        """Create the tracker over its own JSON record file."""
        self._store_path = store_path

    def reset_current(self, raw_host_id: str, session_id: str) -> None:
        """Discard whatever a prior turn left and open a fresh stack on this session."""
        self._write_stacks({**self._read_stacks(), raw_host_id: [session_id]})

    def push_current(self, raw_host_id: str, session_id: str) -> None:
        """Push a newly opened step session as the current one."""
        stacks = self._read_stacks()
        stacks.setdefault(raw_host_id, []).append(session_id)
        self._write_stacks(stacks)

    def pop_current(self, raw_host_id: str) -> None:
        """Pop the current session, restoring the one that dispatched it."""
        stacks = self._read_stacks()
        stack = stacks.get(raw_host_id)
        if not stack:
            return
        stack.pop()
        self._write_stacks(stacks)

    def clear_current(self, raw_host_id: str) -> None:
        """Forget every session of this host conversation — the turn is over."""
        stacks = self._read_stacks()
        stacks[raw_host_id] = []
        self._write_stacks(stacks)

    def resolve_current(self, raw_host_id: str) -> str | None:
        """Resolve the agent session a firing of this host conversation belongs to."""
        stack = self._read_stacks().get(raw_host_id) or []
        return stack[-1] if stack else None

    def resolve_parent(self, raw_host_id: str) -> str | None:
        """Resolve the session that opened the current one, when there is one."""
        stack = self._read_stacks().get(raw_host_id) or []
        return stack[-2] if len(stack) > 1 else None

    def resolve_base(self, raw_host_id: str) -> str | None:
        """Resolve the orchestrator turn session at the bottom of the stack (H6)."""
        stack = self._read_stacks().get(raw_host_id) or []
        return stack[0] if stack else None

    def _read_stacks(self) -> MutableMapping[str, list[str]]:
        if not self._store_path.exists():
            return {}
        recorded: Any = json.loads(self._store_path.read_text(encoding="utf-8"))
        return {key: list(value) for key, value in recorded.items()}

    def _write_stacks(self, stacks: MutableMapping[str, list[str]]) -> None:
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._store_path.write_text(
            json.dumps(stacks, indent=2, sort_keys=True), encoding="utf-8"
        )


__all__ = ["SessionTracker"]
