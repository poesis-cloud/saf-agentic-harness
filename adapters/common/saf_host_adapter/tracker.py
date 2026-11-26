"""The adapter's private record of which harness session each host session is in.

Every host binding names each agent by its own host id inside a subagent (Copilot: the
subagent's `sessionId` = its `agentId`; Claude Code: the `agent_id` every event inside the
subagent carries). Host sessions are therefore never shared between nested agents, so one
flat record per host session is enough — no stack. One FILE per host session keeps parallel
subagents (concurrent hook processes) from losing each other's updates; writes are atomic
(`os.replace`).

Hosts whose subagent-start event does not name the dispatcher (Claude Code) also keep the
dispatches the adapter let through, so that event can be correlated to its dispatcher.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Callable, Iterator

TURN_ORIGIN = "turn"
STEP_ORIGIN = "step"
# A subagent starts within seconds of its dispatch (observed): a dispatch still pending after
# this long was never started (the call failed, was refused, or was interrupted) and must not
# make a later dispatch of the same agent look ambiguous.
PENDING_DISPATCH_TTL_SECONDS = 300
# The outcome H1 recorded for a started subagent, read back when its dispatch returns.
WORKER_REGISTERED = "registered"


class TrackerError(RuntimeError):
    """Raised when a persisted session record cannot be read back."""


class AmbiguousDispatchError(TrackerError):
    """Raised when a started subagent matches pending dispatches of several dispatchers."""


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
    # Stamped when the record is created (H0/H1): the governed workspace every later firing
    # of the session uses, and the harness version that opened it (adapter spec, Session
    # identity). Absent from a record an older harness wrote.
    workspace: str | None = None
    harness_version: str | None = None

    def with_context_injected(self) -> "SessionRecord":
        return replace(self, context_injected=True)

    @property
    def registered(self) -> bool:
        return self.registration_failure is None


class SessionTracker:
    """Persist one `SessionRecord` per sanitized host session id."""

    def __init__(self, state_dir: Path, clock: Callable[[], int] = time.time_ns) -> None:
        self._sessions_dir = state_dir / "sessions"
        self._ignored_returns_dir = state_dir / "ignored-returns"
        self._dispatches_dir = state_dir / "dispatches"
        self._dispatchers_dir = state_dir / "dispatchers"
        self._workers_dir = state_dir / "workers"
        self._clock = clock

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
        pending = directory / "pending" / name
        if not pending.is_file():
            # Nothing was recorded for this sub-agent (an ungoverned conversation, or the
            # bound worker): leave no directory behind.
            return False
        returned = directory / "returned"
        returned.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(pending, returned / name)
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

    # Dispatch correlation (hosts whose subagent start names no dispatcher) ---------------

    def add_pending_dispatch(
        self, root_host_id: str, call_id: str | None, dispatcher_host_id: str, actor: str
    ) -> None:
        """Record a dispatch the adapter let through, until its subagent starts.

        One FILE per dispatch, named so that a listing is in dispatch order; claimed by an
        atomic unlink, so two concurrent subagent starts never claim the same dispatch.
        """
        directory = self._dispatches_dir / _safe_component(root_host_id)
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{self._clock():020d}-{_safe_name(call_id or os.urandom(6).hex())}.json"
        _write_json(directory / name, {"dispatcher": dispatcher_host_id, "actor": actor})

    def drop_pending_dispatch(self, root_host_id: str, call_id: str | None) -> bool:
        """Forget a dispatch whose tool call returned; answer whether it was still pending,
        i.e. its subagent never started (a started subagent claimed it)."""
        if not call_id:
            return False
        directory = self._dispatches_dir / _safe_component(root_host_id)
        dropped = False
        if directory.is_dir():
            for entry in directory.glob(f"*-{_safe_name(call_id)}.json"):
                try:
                    entry.unlink()
                except FileNotFoundError:
                    continue
                dropped = True
        return dropped

    def claim_pending_dispatch(self, root_host_id: str, actor: str) -> str | None:
        """Answer the dispatcher of a subagent of `actor` that just started, and consume it.

        None when no pending dispatch names that actor. Raises `AmbiguousDispatchError`
        when pending dispatches of that actor come from more than one dispatcher, or when a
        pending dispatch cannot be read: the subagent cannot be attributed, so the caller
        fails closed.
        """
        directory = self._dispatches_dir / _safe_component(root_host_id)
        oldest = self._clock() - PENDING_DISPATCH_TTL_SECONDS * 1_000_000_000
        while directory.is_dir():
            matching: list[tuple[Path, str]] = []
            for entry in sorted(directory.glob("*.json")):
                if _stamp(entry) < oldest:
                    entry.unlink(missing_ok=True)  # never started: expired
                    continue
                try:
                    recorded = json.loads(entry.read_text(encoding="utf-8"))
                except FileNotFoundError:
                    continue  # a concurrent start claimed it
                except (OSError, ValueError) as failure:
                    raise AmbiguousDispatchError(
                        f"the pending dispatch {entry.name} cannot be read ({failure}): the "
                        "started subagent cannot be attributed"
                    ) from failure
                if not isinstance(recorded, dict):
                    raise AmbiguousDispatchError(
                        f"the pending dispatch {entry.name} is not a record: the started "
                        "subagent cannot be attributed"
                    )
                if recorded.get("actor") == actor:
                    matching.append((entry, str(recorded.get("dispatcher"))))
            if not matching:
                return None
            if len({dispatcher for _, dispatcher in matching}) > 1:
                raise AmbiguousDispatchError(
                    f"a '{actor}' subagent started while dispatches of it are pending from "
                    "several agents: its dispatcher cannot be told apart"
                )
            entry, dispatcher = matching[0]
            try:
                entry.unlink()
            except FileNotFoundError:
                continue  # a concurrent start claimed it: list again
            return dispatcher
        return None

    def clear_pending_dispatches(self, root_host_id: str) -> None:
        shutil.rmtree(self._dispatches_dir / _safe_component(root_host_id), ignore_errors=True)

    def record_worker(
        self, root_host_id: str, agent_host_id: str, dispatcher_host_id: str | None, outcome: str
    ) -> None:
        """Keep what H1 decided for a started subagent, for H6 to read when its dispatch
        returns naming it (hosts that report the returned subagent)."""
        directory = self._workers_dir / _safe_component(root_host_id)
        directory.mkdir(parents=True, exist_ok=True)
        _write_json(
            directory / _safe_component(agent_host_id),
            {"dispatcher": dispatcher_host_id or "", "outcome": outcome},
        )

    def take_worker(self, root_host_id: str, agent_host_id: str) -> dict[str, str] | None:
        """Read and forget what H1 decided for a returned subagent; None when unknown."""
        path = self._workers_dir / _safe_component(root_host_id) / _safe_component(agent_host_id)
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        path.unlink(missing_ok=True)
        return {str(k): str(v) for k, v in recorded.items()} if isinstance(recorded, dict) else None

    def clear_workers(self, root_host_id: str) -> None:
        shutil.rmtree(self._workers_dir / _safe_component(root_host_id), ignore_errors=True)

    def discard_ignored_return(self, dispatcher_host_id: str, agent_host_id: str) -> None:
        """Forget one sub-agent's ignored-return marker, pending or armed: its return was
        decided by its observed identity instead."""
        directory = self._ignored_returns_dir / _safe_component(dispatcher_host_id)
        for state in ("pending", "returned"):
            (directory / state / _safe_component(agent_host_id)).unlink(missing_ok=True)

    def remember_dispatcher(self, agent_host_id: str, dispatcher_host_id: str) -> None:
        """Keep a subagent's dispatcher until its stop, which does not name it either."""
        self._dispatchers_dir.mkdir(parents=True, exist_ok=True)
        path = self._dispatchers_dir / _safe_component(agent_host_id)
        _write_json(path, {"dispatcher": dispatcher_host_id})

    def dispatcher_of(self, agent_host_id: str) -> str | None:
        path = self._dispatchers_dir / _safe_component(agent_host_id)
        try:
            recorded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        value = recorded.get("dispatcher") if isinstance(recorded, dict) else None
        return str(value) if value else None

    def forget_dispatcher(self, agent_host_id: str) -> None:
        (self._dispatchers_dir / _safe_component(agent_host_id)).unlink(missing_ok=True)

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


def _stamp(entry: Path) -> int:
    """The dispatch time a pending-dispatch file name starts with (0 when unreadable)."""
    head = entry.name.split("-", 1)[0]
    return int(head) if head.isdigit() else 0


def _safe_name(raw: str) -> str:
    """A host tool-call id as a filename component: `[A-Za-z0-9_-]` only."""
    return "".join(character if character.isalnum() or character in "_-" else "-" for character in raw)


def _write_json(path: Path, data: dict[str, str]) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _safe_component(host_id: str) -> str:
    """Refuse a host id that could escape the tracker's directory."""
    if not host_id or "/" in host_id or "\\" in host_id or host_id.startswith("."):
        raise ValueError(f"unsafe host session id: {host_id!r}")
    return host_id


__all__ = ["AmbiguousDispatchError", "TrackerError", "STEP_ORIGIN", "TURN_ORIGIN", "SessionRecord", "SessionTracker"]
