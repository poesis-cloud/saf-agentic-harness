"""Framework layout and adapter state locations, resolved the way the harness resolves them.

The harness core reads `FRAMEWORK_*_DIR` from the process environment first and from
`<FRAMEWORK_DIR>/.env` second. The adapter resolves the SAME values (it needs the
workspace, instructions and skills directories itself) and hands them explicitly to every
harness subprocess, so an agent-scoped hook — which carries no `env` of its own — still
runs the harness against the right layout.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

FRAMEWORK_DIR_VARIABLE = "FRAMEWORK_DIR"
LAYOUT_VARIABLES = (
    "FRAMEWORK_AGENTS_DIR",
    "FRAMEWORK_ARTIFACTS_DIR",
    "FRAMEWORK_INSTRUCTIONS_DIR",
    "FRAMEWORK_SKILLS_DIR",
    "FRAMEWORK_TEMPLATES_DIR",
    "FRAMEWORK_WORKFLOWS_DIR",
    "FRAMEWORK_WORKSPACE_DIR",
)
STATE_DIR_VARIABLE = "SAF_HARNESS_STATE_DIR"
_ENV_FILENAME = ".env"
_STATE_NAMESPACE = "saf-agentic-harness"


class LayoutError(ValueError):
    """Raised when the framework root is unknown or missing."""


@dataclass(frozen=True)
class FrameworkLayout:
    framework_dir: Path
    values: Mapping[str, str]

    def resolve(self, variable: str) -> Path:
        declared = self.values.get(variable)
        return self.framework_dir / declared if declared else self.framework_dir

    @property
    def environment(self) -> dict[str, str]:
        """The explicit `FRAMEWORK_*` environment every harness subprocess runs under."""
        return {FRAMEWORK_DIR_VARIABLE: str(self.framework_dir), **self.values}


def read_env_file(path: Path) -> dict[str, str]:
    """Parse the framework's `.env` exactly as the core `EnvLoader` does: KEY=VALUE lines,
    comments and blanks ignored, a malformed or unreadable file refused (never guessed)."""
    if not path.is_file():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as failure:
        raise LayoutError(f"{path} cannot be read: {failure}") from failure
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator == "" or key.strip() == "":
            raise LayoutError(f"{path} line {line_number} is malformed: expected KEY=VALUE")
        values[key.strip()] = value.strip()
    return values


def resolve_layout(
    framework_dir: str | None,
    environment: Mapping[str, str] | None = None,
    workspace_dir: str | None = None,
) -> FrameworkLayout:
    """Resolve the layout: `--framework-dir`/`--workspace-dir` > environment > `.env`.

    The workspace (the governed portfolio tree) may live outside the framework checkout;
    an absolute `workspace_dir` is handed to the harness as `FRAMEWORK_WORKSPACE_DIR`.
    """
    environment = os.environ if environment is None else environment
    raw = framework_dir or environment.get(FRAMEWORK_DIR_VARIABLE)
    if not raw:
        raise LayoutError(
            "no framework root: pass --framework-dir or set FRAMEWORK_DIR"
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise LayoutError(f"the framework root does not exist: {root}")
    declared = read_env_file(root / _ENV_FILENAME)
    values = {
        variable: value
        for variable in LAYOUT_VARIABLES
        if (value := environment.get(variable, declared.get(variable)))
    }
    if workspace_dir:
        values["FRAMEWORK_WORKSPACE_DIR"] = str(Path(workspace_dir).expanduser())
    return FrameworkLayout(framework_dir=root.resolve(), values=values)


def resolve_state_dir(adapter_id: str, environment: Mapping[str, str] | None = None) -> Path:
    """The adapter's per-user runtime state directory — outside every repository.

    `SAF_HARNESS_STATE_DIR` wins; otherwise `%LOCALAPPDATA%` on Windows and
    `$XDG_STATE_HOME` (default `~/.local/state`) elsewhere.
    """
    environment = os.environ if environment is None else environment
    explicit = environment.get(STATE_DIR_VARIABLE)
    if explicit:
        return Path(explicit) / adapter_id
    if os.name == "nt" and environment.get("LOCALAPPDATA"):
        return Path(environment["LOCALAPPDATA"]) / _STATE_NAMESPACE / adapter_id
    state_home = environment.get("XDG_STATE_HOME")
    base = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return base / _STATE_NAMESPACE / adapter_id


__all__ = [
    "FRAMEWORK_DIR_VARIABLE",
    "LAYOUT_VARIABLES",
    "FrameworkLayout",
    "LayoutError",
    "read_env_file",
    "resolve_layout",
    "resolve_state_dir",
]
