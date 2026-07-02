"""A run journal / session ledger (JSONL) — the append-only, `after`-ordered record of commands."""

from __future__ import annotations

from typing import Any


class LogEntry:
    """One journal line, normalized over both shapes: the enveloped form
    (`{command, step, status, payload:{...}}`) and the legacy flat form
    (`{step, status, conditions}` / a flat hook line). Accessors read the envelope first, then
    fall back to the payload or the legacy top-level keys, so readers never branch on the shape."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        payload = raw.get("payload")
        self.payload: dict[str, Any] = payload if isinstance(payload, dict) else {}

    def _get(self, *keys: str) -> Any:
        for key in keys:
            if self.raw.get(key):
                return self.raw[key]
            if self.payload.get(key):
                return self.payload[key]
        return None

    @property
    def command(self) -> str | None:
        return self.raw.get("command")

    @property
    def actor(self) -> str | None:
        value = self._get("actor")
        return str(value) if value else None

    @property
    def run(self) -> str | None:
        value = self._get("run")
        return str(value) if value else None

    @property
    def step(self) -> str | None:
        value = self._get("step")
        return str(value) if value else None

    @property
    def orchestration(self) -> str | None:
        value = self._get("orchestration")
        return str(value) if value else None

    @property
    def unit(self) -> str | None:
        value = self._get("unit", "unit_id")  # envelope `unit`, legacy `unit_id`
        return str(value) if value else None

    @property
    def status(self) -> str | None:
        value = self._get("status")
        return str(value) if value else None

    @property
    def outputs(self) -> list[str]:
        value = self._get("outputs")
        return list(value) if isinstance(value, list) else []

    @property
    def permission(self) -> str | None:
        value = self._get("permission")
        return str(value) if value else None


class Log:
    """The event plane: the parsed lines of one run's JSONL journal.

    The `LogMapper` maps the file to this entity (or to None when no log is supplied).
    `executed_steps` applies latest-wins replay semantics — a step's latest line decides
    whether it currently counts as complete. `by_step` / `replay_steps` reconstruct the run
    from the journal by grouping command entries under their step.
    """

    def __init__(self, lines: list[dict[str, Any]]) -> None:
        self.lines = lines

    def entries(self) -> list[LogEntry]:
        return [LogEntry(line) for line in self.lines]

    def executed_steps(self) -> list[str]:
        """Steps whose LATEST step-bearing line is 'completed' (replay re-opens a step)."""
        latest: dict[str, str] = {}
        for entry in self.entries():
            if entry.step:
                latest[entry.step] = str(entry.status)
        return [step for step, status in latest.items() if status == "completed"]

    def by_step(self) -> dict[str, list[LogEntry]]:
        """Group every step-bearing entry under its step id, preserving file order — so a step is
        reconstructed from the command entries its session brackets (one step → several entries)."""
        groups: dict[str, list[LogEntry]] = {}
        for entry in self.entries():
            if entry.step:
                groups.setdefault(entry.step, []).append(entry)
        return groups

    def replay_steps(self) -> list[str]:
        """The run's step sequence: each step id in first-seen order across the journal."""
        sequence: list[str] = []
        for entry in self.entries():
            if entry.step and entry.step not in sequence:
                sequence.append(entry.step)
        return sequence
