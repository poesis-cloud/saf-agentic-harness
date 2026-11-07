"""The adapter's private record of which harness session each host session is in.

Captured payloads show that inside a subagent EVERY event carries `sessionId` = that
subagent's own `agentId` (with `parentAgentId` naming the dispatcher). Host sessions are
therefore never shared between nested agents, so one flat record per host session is
enough — no stack. One FILE per host session keeps parallel subagents (concurrent hook
processes) from losing each other's updates; writes are atomic (`os.replace`).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterator

TURN_ORIGIN = "turn"
STEP_ORIGIN = "step"


class TrackerError(RuntimeError):
    """Raised when a persisted session record cannot be read back."""


@dataclass(frozen=True)
class SessionRecord:
    """The harness session a host session is registered as.

    `registration_failure` set means `start-session` answered an ERROR (or no report) for
    this host session: it is NOT registered, yet it is not ungoverned either — only an
    explicit `not-applicable` is. The adapter denies its writes and dispatches (fail closed).
    """

    session_id: str
    agent: str
    origin: str
    parent_session_id: str | None = None
    parent_host_id: str | None = None
    context_injected: bool = False
    registration_failure: str | None = None

    def with_context_injected(self) -> "SessionRecord":
        return replace(self, context_injected=True)

    @property
    def registered(self) -> bool:
        return self.registration_failure is None


class SessionTracker:
    """Persist one `SessionRecord` per sanitized host session id."""

    def __init__(self, state_dir: Path) -> None:
        self._sessions_dir = state_dir / "sessions"
        self._ignored_returns_dir = state_dir / "ignored-returns"

    def read(self, host_id: str) -> SessionRecord | None:
        path = self._path(host_id)
        if not path.is_file():
            return None
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
            return SessionRecord(**recorded)
        except (ValueError, TypeError) as failure:
            # A governed session must never silently become an ungoverned one: the entry
            # point turns this into a preToolUse DENY (fail closed).
            raise TrackerError(f"session record {path.name} is unreadable: {failure}") from failure

    def write(self, host_id: str, record: SessionRecord) -> None:
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=self._sessions_dir, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(asdict(record), handle, sort_keys=True)
            os.replace(temporary, self._path(host_id))
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def delete(self, host_id: str) -> None:
        self._path(host_id).unlink(missing_ok=True)
        self.clear_ignored_returns(host_id)

    def mark_ignored_return(self, dispatcher_host_id: str, agent_host_id: str) -> None:
        """Record a sub-agent whose return must not conclude its dispatcher's step.

        Adapter spec H6: the return of a sub-agent whose worker-session registration was
        refused — or that was never registered as the step's worker — does not trigger
        `check-step-postconditions`. The marker stays PENDING until that very sub-agent
        stops (`arm_ignored_return`). One FILE per sub-agent, moved and consumed
        atomically, so concurrent hook processes never lose or share a marker.
        """
        pending = self._ignored_returns_dir / _safe_component(dispatcher_host_id) / "pending"
        pending.mkdir(parents=True, exist_ok=True)
        (pending / _safe_component(agent_host_id)).touch()

    def arm_ignored_return(self, dispatcher_host_id: str, agent_host_id: str) -> bool:
        """At that sub-agent's own stop, make its marker consumable by the next return.

        The host fires a sub-agent's `subagentStop` before the dispatcher's return of that
        same dispatch (host fact 6), so an armed marker always belongs to a sub-agent that
        has already stopped — never to the bound worker, which is never marked.
        """
        directory = self._ignored_returns_dir / _safe_component(dispatcher_host_id)
        name = _safe_component(agent_host_id)
        returned = directory / "returned"
        returned.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(directory / "pending" / name, returned / name)
        except FileNotFoundError:
            return False
        return True

    def consume_ignored_return(self, dispatcher_host_id: str) -> bool:
        """Consume one armed ignored return of this dispatcher, if any.

        While the step's bound worker runs, every return of this dispatcher is that of a
        stopped, ignored sub-agent, whose marker is armed. Consumption is an atomic
        unlink: two concurrent returns never consume the same marker.
        """
        returned = self._ignored_returns_dir / _safe_component(dispatcher_host_id) / "returned"
        # Re-list after losing every unlink race: a concurrent return may have consumed
        # the markers seen while another sub-agent armed a new one meanwhile.
        while returned.is_dir():
            markers = sorted(returned.iterdir())
            if not markers:
                return False
            for marker in markers:
                try:
                    marker.unlink()
                except FileNotFoundError:
                    continue
                return True
        return False

    def clear_ignored_returns(self, host_id: str) -> None:
        """Forget every pending ignored return of a session that closes or restarts."""
        shutil.rmtree(
            self._ignored_returns_dir / _safe_component(host_id), ignore_errors=True
        )

    def descendants(self, host_id: str) -> tuple[str, ...]:
        """Answer every host session registered (transitively) under `host_id`."""
        children: dict[str, list[str]] = {}
        for child_id, record in self._records():
            if record.parent_host_id:
                children.setdefault(record.parent_host_id, []).append(child_id)
        found: list[str] = []
        pending = list(children.get(host_id, ()))
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.append(current)
            pending.extend(children.get(current, ()))
        return tuple(found)

    def _records(self) -> Iterator[tuple[str, SessionRecord]]:
        if not self._sessions_dir.is_dir():
            return
        for path in sorted(self._sessions_dir.glob("*.json")):
            try:
                record = self.read(path.stem)
            except TrackerError:
                continue
            if record is not None:
                yield path.stem, record

    def _path(self, host_id: str) -> Path:
        if not host_id or "/" in host_id or "\\" in host_id or host_id.startswith("."):
            raise ValueError(f"unsafe host session id: {host_id!r}")
        return self._sessions_dir / f"{host_id}.json"


def _safe_component(host_id: str) -> str:
    """Refuse a host id that could escape the tracker's directory."""
    if not host_id or "/" in host_id or "\\" in host_id or host_id.startswith("."):
        raise ValueError(f"unsafe host session id: {host_id!r}")
    return host_id


__all__ = ["TrackerError", "STEP_ORIGIN", "TURN_ORIGIN", "SessionRecord", "SessionTracker"]
