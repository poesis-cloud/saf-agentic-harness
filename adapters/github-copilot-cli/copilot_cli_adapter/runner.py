"""The adapter's ONE edge into the harness core: its command API (adapter spec I15).

Cross-platform: the harness is run with the SAME interpreter that runs the adapter
(`sys.executable` — the venv the installer recorded), never a `python3` that does not
exist on Windows; I/O is pinned to UTF-8 so a Windows console code page cannot corrupt a
report.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


class HarnessCommandError(RuntimeError):
    """Raised when a harness command produced no usable report.

    A gating boundary renders it as a DENIAL — a missing outcome is never an allow.
    """


class CommandRunner(Protocol):
    def run_function(self, function: str, inquiry: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run one function command with its inquiry and answer its report."""


ProcessRunner = Callable[[Sequence[str], Mapping[str, str]], "tuple[int, str, str]"]


def run_subprocess(argv: Sequence[str], environment: Mapping[str, str]) -> tuple[int, str, str]:
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(environment),
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


class SubprocessCommandRunner:
    """Invoke `harness.py <function>` as its own process, one command per function."""

    def __init__(
        self,
        harness_entrypoint: Path,
        layout_environment: Mapping[str, str],
        run_process: ProcessRunner | None = None,
        python_executable: str | None = None,
    ) -> None:
        self._harness_entrypoint = harness_entrypoint
        self._layout_environment = dict(layout_environment)
        self._run_process = run_process or run_subprocess
        self._python_executable = python_executable or sys.executable

    def run_function(self, function: str, inquiry: Mapping[str, Any]) -> Mapping[str, Any]:
        argv = [
            self._python_executable,
            str(self._harness_entrypoint),
            function,
            *render_flags(inquiry),
        ]
        environment = {
            **os.environ,
            **self._layout_environment,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        }
        exit_code, stdout, stderr = self._run_process(argv, environment)
        if exit_code != 0:
            raise HarnessCommandError(
                f"harness command '{function}' failed (exit {exit_code}): {stderr.strip()}"
            )
        try:
            report = json.loads(stdout)
        except ValueError as failure:
            raise HarnessCommandError(
                f"harness command '{function}' answered no report: {stdout.strip()[:200]!r}"
            ) from failure
        if not isinstance(report, Mapping):
            raise HarnessCommandError(
                f"harness command '{function}' answered {type(report).__name__}, not a report"
            )
        return report


def render_flags(inquiry: Mapping[str, Any]) -> list[str]:
    """Render an inquiry object as the command's kebab-case flags."""
    flags: list[str] = []
    for key, value in inquiry.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            flag = f"--{_kebab(key).removesuffix('s')}"
            for item in value:
                flags.extend((flag, str(item)))
            continue
        flags.extend((f"--{_kebab(key)}", str(value)))
    return flags


def _kebab(key: str) -> str:
    return _CAMEL_BOUNDARY.sub("-", key).lower()


__all__ = [
    "CommandRunner",
    "HarnessCommandError",
    "SubprocessCommandRunner",
    "render_flags",
    "run_subprocess",
]
