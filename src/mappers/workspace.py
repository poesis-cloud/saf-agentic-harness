"""Workspace — the filesystem context of the DATA plane the harness checks.

Holds the framework root (where the embedding framework lives — needed to resolve conf/ and to
label findings) and the workspace root (the data the harness CRUDs its domain entities from —
this framework calls it the portfolio). Framework-side layout (skills, agents, schema registry)
is configuration — declared in conf/framework.conf.yaml and exposed by the config plane
(`FrameworkLayout`), never resolved here: the workspace is NOT the framework.
"""

from __future__ import annotations

from pathlib import Path

from text import format_template


class Workspace:
    def __init__(self, framework_root: Path, workspace_root: Path | None = None) -> None:
        self.framework_root = framework_root
        self._workspace_root = workspace_root

    @classmethod
    def detect(cls, framework_root: Path | None = None, workspace_root: Path | None = None) -> "Workspace":
        resolved_framework = (framework_root or cls.default_framework_root()).resolve()
        resolved_workspace = (workspace_root or (resolved_framework / "workspace")).resolve()
        return cls(resolved_framework, resolved_workspace)

    @classmethod
    def default_framework_root(cls) -> Path:
        script = Path(__file__).resolve()
        for parent in script.parents:
            if (parent / "plugin.json").is_file() or (parent / "conf").is_dir():
                return parent
        # __file__ = harness/src/mappers/workspace.py; the last-ditch fallback walks up past
        # src/mappers/ and the harness project when no plugin.json / conf marker is found
        # (the marker walk above is the normal, depth-independent path).
        return script.parents[7]

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root if self._workspace_root is not None else self.framework_root / "workspace"

    @property
    def workspace_base(self) -> Path:
        """The directory that CONTAINS the workspace folder — the base against which repo-root-
        relative artifact refs resolve. Tracks ``workspace_root`` so artifact reads follow the
        workspace data, not the framework code; defaults to the framework root."""
        return self.workspace_root.parent

    def session_ledger(self, session_id: str | None) -> Path:
        """The per-session run ledger (JSONL): the single append-only record the hook funnel writes
        and `check-step` appends to, keyed by the host session id, so session-open, every write,
        each step, and session-close land in one file. A missing id falls back to the shared
        'session' ledger — the same fallback the hook uses when the host supplies no id — so a manual
        or standalone invocation still logs to the place the session-close review reads."""
        sid = str(session_id).replace("/", "-") if session_id else "session"
        return self.workspace_root / "logs" / "hooks" / f"{sid}.jsonl"

    def run_journal(self, run_id: str | None) -> Path:
        """The per-run journal (JSONL): the single append-only run trace (`workspace/logs/<run>.jsonl`)
        spanning the driver and its dispatched step sessions, one entry per command. The `orchestrate`
        driver writes its action entries here; the per-session hook streams correlate to it by run +
        session. A missing id falls back to the shared 'run' journal so a standalone drive still traces."""
        rid = str(run_id).replace("/", "-") if run_id else "run"
        return self.workspace_root / "logs" / f"{rid}.jsonl"

    def run_journals(self) -> list[Path]:
        """Every per-run journal (`workspace/logs/*.jsonl`), oldest first by mtime so the LAST match
        when scanning is the most recent dispatch. The per-session hook streams under `logs/hooks/`
        are NOT run journals, so the directory is skipped (only top-level *.jsonl are runs)."""
        logs_dir = self.workspace_root / "logs"
        if not logs_dir.is_dir():
            return []
        files = [p for p in logs_dir.glob("*.jsonl") if p.is_file()]
        return sorted(files, key=lambda p: p.stat().st_mtime)

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8", errors="replace")

    def label(self, path: Path, base: Path) -> str:
        try:
            return str(path.relative_to(base))
        except ValueError:
            return str(path)

    def resolve_trace_path(self, template: str, **variables: str) -> Path:
        rendered = Path(format_template(template, **variables))
        if rendered.is_absolute():
            return rendered
        if rendered.parts and rendered.parts[0] == "workspace":
            rendered = Path(*rendered.parts[1:]) if len(rendered.parts) > 1 else Path()
        return self.workspace_root / rendered
