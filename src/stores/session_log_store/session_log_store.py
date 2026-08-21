"""The store owning the session logs: one session = one writer = one file."""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Iterator, Sequence

from errors import InquiryError, StateError
from stores.session_log_store.log import Log
from stores.session_log_store.log_entry import LogEntry
from stores.session_log_store.workflow_instance_view import WorkflowInstanceView
from utils.jsonl_store import JsonlStore
from utils.schema_validator import SchemaValidator

_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts"
_LOG_ENTRY_CONTRACT_ID = "gsmarc://saf/contracts/log-entry/v1"
_LOG_FILE_SUFFIX = ".log.jsonl"
_CROCKFORD_BASE32_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_INSTANCE_MINT_LENGTH = 6
_SESSION_ID_PATTERN = re.compile(r"^[a-z0-9-]+$")

_default_validator: SchemaValidator | None = None


def _load_default_validator() -> SchemaValidator:
    """Compile the repository contracts once and reuse the validator."""
    global _default_validator
    if _default_validator is None:
        _default_validator = SchemaValidator.compile_contracts(
            sorted(_CONTRACTS_DIR.rglob("*.schema.json"))
        )
    return _default_validator


class SessionLogStore:
    """Own the log side of the workspace: create, append, load, and derive."""

    def __init__(
        self,
        workspace_dir: str | Path,
        jsonl_store: JsonlStore | None = None,
        schema_validator: SchemaValidator | None = None,
    ) -> None:
        """Create the store over `<workspace>/logs/`, compiling contracts once."""
        self._workspace_dir = Path(workspace_dir)
        self._jsonl = jsonl_store or JsonlStore()
        self._validator = schema_validator or _load_default_validator()

    def create_session_log(self, entry: LogEntry) -> Log:
        """Write function 0's registration entry as a new log's first line."""
        session_id = entry.report.context.session_id
        # The store cannot name a file for an unsafe id, so the filename
        # constraint is settled before any entry-shape concern.
        path = self._resolve_log_path(session_id)
        self._require_valid_entry(entry)
        if path.exists():
            raise StateError(
                "session-conflict",
                f"Session log for '{session_id}' already exists.",
                False,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        self._jsonl.append_entry(path, entry.to_dict())
        return Log(session_id=session_id, entries=(entry,))

    def append_log_entry(self, session_id: str, entry: LogEntry) -> None:
        """Append one contract-bound entry to an existing session log."""
        path = self._resolve_log_path(session_id)
        if not path.exists():
            raise StateError(
                "session-unregistered",
                f"No session log exists for '{session_id}'.",
                False,
            )
        self._require_valid_entry(entry)
        self._jsonl.append_entry(path, entry.to_dict())

    def load_session_log(self, session_id: str) -> Log:
        """Hydrate one session's log in file order."""
        path = self._resolve_log_path(session_id)
        if not path.exists():
            raise StateError(
                "session-unregistered",
                f"No session log exists for '{session_id}'.",
                False,
            )
        entries = tuple(LogEntry.from_dict(raw) for raw in self._jsonl.load_entries(path))
        return Log(session_id=session_id, entries=entries)

    def mint_workflow_instance_id(self, workflow_slug: str) -> str:
        """Mint an instance id: the slug plus an uppercase Crockford-base32 mint."""
        mint = "".join(
            secrets.choice(_CROCKFORD_BASE32_ALPHABET)
            for _ in range(_INSTANCE_MINT_LENGTH)
        )
        return f"{workflow_slug}-{mint}"

    def load_workflow_instance_view(self, workflow_instance_id: str) -> WorkflowInstanceView:
        """Assemble the cross-log instance view, timestamp-ordered."""
        matching = [
            entry
            for entry in self._iter_all_entries()
            if entry.report.context.workflow_instance_id == workflow_instance_id
        ]
        matching.sort(key=lambda entry: entry.timestamp)
        return WorkflowInstanceView(
            workflow_instance_id=workflow_instance_id,
            entries=tuple(matching),
        )

    def find_latest_open_instance(
        self,
        workflow_slug: str,
        *,
        workflow_steps: Sequence[str],
    ) -> str | None:
        """Find the workflow's most recently active still-open instance, if any.

        Open means at least one authored step is not journaled executed. Among
        open instances the latest entry `timestamp` wins; an identical latest
        `timestamp` breaks toward the lexicographically lowest instance id.
        """
        prefix = f"{workflow_slug}-"
        grouped: dict[str, list[LogEntry]] = {}
        for entry in self._iter_all_entries():
            instance_id = entry.report.context.workflow_instance_id
            if instance_id is not None and instance_id.startswith(prefix):
                grouped.setdefault(instance_id, []).append(entry)

        open_candidates: list[tuple[str, str]] = []
        for instance_id, entries in grouped.items():
            entries.sort(key=lambda entry: entry.timestamp)
            view = WorkflowInstanceView(
                workflow_instance_id=instance_id,
                entries=tuple(entries),
            )
            executed = view.list_executed_steps()
            if any(step not in executed for step in workflow_steps):
                open_candidates.append((entries[-1].timestamp, instance_id))

        if not open_candidates:
            return None
        open_candidates.sort(key=lambda candidate: candidate[1])
        open_candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        return open_candidates[0][1]

    def _resolve_log_path(self, session_id: str) -> Path:
        """Resolve one session's log file path under the workspace logs dir.

        Spec (Logging, Sanitization): `sessionId` becomes a log filename, so the safe-slug
        constraint is the store's — this is the one choke point every read and write funnels
        through, which is what makes it structural rather than bypassable. The callers'
        own `_require_inquiry_slugs` stays as fast-fail at the inquiry boundary.
        """
        if not isinstance(session_id, str) or _SESSION_ID_PATTERN.match(session_id) is None:
            raise InquiryError(
                "invalid-inquiry",
                f"Session id '{session_id}' is not a safe slug and cannot name a log file.",
                False,
            )
        return self._workspace_dir / "logs" / f"{session_id}{_LOG_FILE_SUFFIX}"

    def _iter_all_entries(self) -> Iterator[LogEntry]:
        """Iterate every entry across every session log, in file order per log."""
        logs_dir = self._workspace_dir / "logs"
        if not logs_dir.is_dir():
            return
        for path in sorted(logs_dir.glob(f"*{_LOG_FILE_SUFFIX}")):
            for raw in self._jsonl.load_entries(path):
                yield LogEntry.from_dict(raw)

    def _require_valid_entry(self, entry: LogEntry) -> None:
        """Reject an entry the log-entry contract does not admit."""
        records = self._validator.validate_instance(_LOG_ENTRY_CONTRACT_ID, entry.to_dict())
        if records:
            first = records[0]
            location = f" at '{first.path}'" if first.path else ""
            raise StateError(
                "invalid-log-entry",
                f"Log entry violates the log-entry contract{location}: {first.message}",
                False,
            )


__all__ = ["SessionLogStore"]
