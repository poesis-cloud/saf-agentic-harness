"""The ONE harness command line an agent may run, and how to recognize it (H4).

The adapter knows the interpreter it runs under (the venv the installer recorded) and the
harness entry point next to it, so it can both TELL an orchestrator the exact line to use
(injected with its workflow context) and REFUSE any look-alike: a `harness.py` elsewhere
on disk is not the harness, whatever its name.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .paths import resolve_host_path

# Any interpreter spelling an agent may use — bare (`python`, `py -3`) or a path to one; what
# is pinned is the harness entry point, not the interpreter that runs it.
_INTERPRETER_NAME = re.compile(r"python(?:3(?:\.[0-9]+)?)?|py")


def unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _posix_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


@dataclass(frozen=True)
class HarnessInvocation:
    python: str
    harness: str
    windows: bool
    framework_dir: str = ""
    workspace_dir: str = ""
    environment: Mapping[str, str] = field(default_factory=dict)

    def example(self, arguments: str) -> str:
        if self.windows:
            return (
                f"& {_powershell_quote(self.python)} {_powershell_quote(self.harness)} "
                f"{arguments}"
            )
        return f"{_posix_quote(self.python)} {_posix_quote(self.harness)} {arguments}"

    def canonical(self, function: str, attribution: Sequence[str], flags: str, powershell: bool) -> str:
        """The adapter-authored command actually run: the agent's shell never inherited the
        hooks' `env`, so the layout travels in the command; the interpreter is the installed
        one (whatever spelling the agent used); the agent contributes only `flags`, already
        validated as plain `--flag [value]` tokens."""
        call = " ".join((function, *attribution)) + flags
        if powershell:
            assignments = "".join(
                f"$env:{name}={_powershell_quote(value)}; "
                for name, value in sorted(self.environment.items())
            )
            return (
                f"{assignments}& {_powershell_quote(self.python)} "
                f"{_powershell_quote(self.harness)} {call}"
            )
        assignments = "".join(
            f"{name}={_posix_quote(value)} " for name, value in sorted(self.environment.items())
        )
        return f"{assignments}{_posix_quote(self.python)} {_posix_quote(self.harness)} {call}"

    def is_harness(self, raw: str, cwd: str | None) -> bool:
        return resolve_host_path(raw, cwd) == resolve_host_path(self.harness, None)

    def is_interpreter(self, raw: str) -> bool:
        if resolve_host_path(raw, None) == resolve_host_path(self.python, None):
            return True
        name = ntpath.basename(raw) if self.windows or "\\" in raw else posixpath.basename(raw)
        return bool(_INTERPRETER_NAME.fullmatch(name.lower().removesuffix(".exe")))

    def hint(self) -> str:
        lines = ["# SAFe agentic harness", ""]
        if self.framework_dir:
            lines.append(f"- Framework root (FRAMEWORK_DIR): {self.framework_dir}")
        if self.workspace_dir:
            lines.append(f"- Governed workspace (FRAMEWORK_WORKSPACE_DIR): {self.workspace_dir}")
        if len(lines) > 2:
            lines.append("")
        lines.extend(
            (
                "Run a harness command with the shell tool as ONE plain command (never chained, "
                "piped or redirected; one harness command per tool call), and never pass "
                "--session-id or --parent-session-id — the adapter attributes the call itself:",
                "",
                f"    {self.example('resolve-step --workflow-slug <workflow-slug>')}",
                f"    {self.example('resolve-step-model')}",
            )
        )
        return "\n".join(lines)


__all__ = ["HarnessInvocation", "unquote"]
