"""LogMapper — reads a run log into a `Log`, and appends step lines to it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from models import Log
from .workspace import Workspace


class LogMapper:
    """The data-mapper for the JSONL run log (the event plane).

    `read` returns a `Log` (parsed lines, latest-wins) or `None` when no log is supplied —
    the caller treats `None` as "predecessor checks unavailable". `append` writes one
    canonical step line. Line-level schema validation (with line numbers) is the
    `LogChecker`'s job; this mapper only parses well-formed lines.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    def read(self, path: Path | None) -> Log | None:
        if path is None or not path.is_file():
            return None
        lines: list[dict[str, Any]] = []
        for raw in self.workspace.read_text(path).splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                lines.append(entry)
        return Log(lines)

    def append(self, path: Path, line: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line) + "\n")

    def append_entry(
        self,
        path: Path,
        *,
        command: str,
        payload: dict[str, Any],
        trigger: str | None = None,
        run: str | None = None,
        session: str | None = None,
        orchestration: str | None = None,
        step: str | None = None,
        unit: str | None = None,
        actor: str | None = None,
        status: str | None = None,
        ts: str | None = None,
    ) -> None:
        """Append one enveloped journal entry: the shared envelope (command + run/step header) wrapping
        the command's typed `payload`. The envelope ties the entry to its run + step so the journal
        replays (group by `step`); the payload is the command's report or action. Only set envelope
        fields are emitted (a null field is omitted)."""
        entry: dict[str, Any] = {"command": command}
        for key, value in (
            ("trigger", trigger),
            ("run", run),
            ("session", session),
            ("orchestration", orchestration),
            ("step", step),
            ("unit", unit),
            ("actor", actor),
            ("status", status),
            ("ts", ts),
        ):
            if value:
                entry[key] = value
        entry["payload"] = payload
        self.append(path, entry)
