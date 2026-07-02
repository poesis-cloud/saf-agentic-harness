"""Workspace — the filesystem context shared by every repository.

Holds the framework + portfolio roots and resolves the well-known locations (skills root,
harness + schema dirs) and labels. Injecting one Workspace replaces the framework_root /
portfolio_root that the old procedural harness threaded through every call.
"""

from __future__ import annotations

from pathlib import Path

from text import format_template


class Workspace:
    def __init__(self, framework_root: Path, portfolio_root: Path | None = None) -> None:
        self.framework_root = framework_root
        self._portfolio_root = portfolio_root

    @classmethod
    def detect(cls, framework_root: Path | None = None, portfolio_root: Path | None = None) -> "Workspace":
        resolved_framework = (framework_root or cls.default_framework_root()).resolve()
        resolved_portfolio = (portfolio_root or (resolved_framework / "portfolio")).resolve()
        return cls(resolved_framework, resolved_portfolio)

    @classmethod
    def default_framework_root(cls) -> Path:
        script = Path(__file__).resolve()
        for parent in script.parents:
            if (parent / "plugin.json").is_file() or (parent / ".github" / "skills").is_dir():
                return parent
        # __file__ = harness/src/mappers/workspace.py; the last-ditch fallback walks up past
        # src/mappers/ and the harness project when no plugin.json / skills marker is found
        # (the marker walk above is the normal, depth-independent path).
        return script.parents[7]

    @property
    def skills_root(self) -> Path:
        # Resolve the skills root structurally from the deployment shape — no marker file. A host
        # integration nests skills under `.github/skills/` or a `skills/` subdir; the standalone
        # ("colocated") shape is the framework root itself, where harness/, layers/, and schemas are its
        # direct children — confirmed by the presence of harness/ rather than a sentinel, so the
        # framework's own structure stays independent of where it is mounted.
        github_root = self.framework_root / ".github" / "skills"
        if github_root.is_dir():
            return github_root
        plugin_root = self.framework_root / "skills"
        if plugin_root.is_dir():
            return plugin_root
        if (self.framework_root / "harness").is_dir():
            return self.framework_root
        raise FileNotFoundError(f"no skills directory found under {self.framework_root}")

    @property
    def portfolio_root(self) -> Path:
        return self._portfolio_root if self._portfolio_root is not None else self.framework_root / "portfolio"

    @property
    def portfolio_base(self) -> Path:
        """The directory that CONTAINS the ``portfolio/`` folder — the base against which repo-root-
        relative artifact refs (``portfolio/...``) resolve. Tracks ``portfolio_root`` so artifact reads
        follow the portfolio data, not the framework code; defaults to the framework root (portfolio
        colocated at ``<framework-root>/portfolio``)."""
        return self.portfolio_root.parent

    @property
    def harness_dir(self) -> Path:
        return self.skills_root / "harness"

    @property
    def schemas_dir(self) -> Path:
        return self.harness_dir / "schemas"

    def session_ledger(self, session_id: str | None) -> Path:
        """The per-session run ledger (JSONL): the single append-only record the hook funnel writes
        and `check-step` appends to, keyed by the host session id, so session-open, every write,
        each step, and session-close land in one file. A missing id falls back to the shared
        'session' ledger — the same fallback the hook uses when the host supplies no id — so a manual
        or standalone invocation still logs to the place the session-close review reads."""
        sid = str(session_id).replace("/", "-") if session_id else "session"
        return self.portfolio_root / "logs" / "hooks" / f"{sid}.jsonl"

    def run_journal(self, run_id: str | None) -> Path:
        """The per-run journal (JSONL): the single append-only run trace (`portfolio/logs/<run>.jsonl`)
        spanning the driver and its dispatched step sessions, one entry per command. The `orchestrate`
        driver writes its action entries here; the per-session hook streams correlate to it by run +
        session. A missing id falls back to the shared 'run' journal so a standalone drive still traces."""
        rid = str(run_id).replace("/", "-") if run_id else "run"
        return self.portfolio_root / "logs" / f"{rid}.jsonl"

    def run_journals(self) -> list[Path]:
        """Every per-run journal (`portfolio/logs/*.jsonl`), oldest first by mtime so the LAST match
        when scanning is the most recent dispatch. The per-session hook streams under `logs/hooks/`
        are NOT run journals, so the directory is skipped (only top-level *.jsonl are runs)."""
        logs_dir = self.portfolio_root / "logs"
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
        if rendered.parts and rendered.parts[0] == "portfolio":
            rendered = Path(*rendered.parts[1:]) if len(rendered.parts) > 1 else Path()
        return self.portfolio_root / rendered
