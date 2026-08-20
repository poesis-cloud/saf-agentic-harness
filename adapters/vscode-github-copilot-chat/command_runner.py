"""The adapter's ONE edge into the harness core: its command API."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")
_PYTHON_EXECUTABLE = "python3"


class HarnessCommandError(RuntimeError):
    """Raised when a harness command produced no usable report.

    Spec (adapter, H2 invariant 1 / H3 invariant 3): the caller renders this as a DENIAL
    at a gating boundary — a missing outcome is never an implicit allow.
    """


class CommandRunner(Protocol):
    """Invoke one harness function command and answer its contract report.

    Spec (adapter, I15): the adapter depends on the command API and nothing else — no
    `services`, no `stores`, no `config`.
    """

    def run_function(
        self, function: str, inquiry: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Run one function command with its inquiry and answer its report."""


ProcessRunner = Callable[[Sequence[str]], "tuple[int, str, str]"]


def run_subprocess(argv: Sequence[str]) -> tuple[int, str, str]:
    """Run one command as a child process, answering exit code, stdout, and stderr."""
    completed = subprocess.run(list(argv), capture_output=True, text=True, check=False)
    return completed.returncode, completed.stdout, completed.stderr


class SubprocessCommandRunner:
    """Invoke `harness.py <function>` as its own process, one command per function."""

    def __init__(
        self,
        harness_entrypoint: Path,
        run_process: ProcessRunner | None = None,
        python_executable: str = _PYTHON_EXECUTABLE,
    ) -> None:
        """Create the runner over the harness entrypoint and its process boundary."""
        self._harness_entrypoint = harness_entrypoint
        self._run_process = run_process or run_subprocess
        self._python_executable = python_executable

    def run_function(
        self, function: str, inquiry: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        """Run one harness function command and parse its report."""
        argv = [
            self._python_executable,
            str(self._harness_entrypoint),
            function,
            *_render_flags(inquiry),
        ]
        exit_code, stdout, stderr = self._run_process(argv)
        if exit_code != 0:
            raise HarnessCommandError(
                f"harness command '{function}' failed (exit {exit_code}): {stderr.strip()}"
            )
        try:
            report = json.loads(stdout)
        except ValueError as failure:
            raise HarnessCommandError(
                f"harness command '{function}' answered no report: {stdout.strip()!r}"
            ) from failure
        if not isinstance(report, Mapping):
            raise HarnessCommandError(
                f"harness command '{function}' answered {type(report).__name__}, not a report"
            )
        return report


def _render_flags(inquiry: Mapping[str, Any]) -> list[str]:
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
    "run_subprocess",
]
