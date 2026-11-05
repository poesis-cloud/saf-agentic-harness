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
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


class HarnessCommandError(RuntimeError):
    """Raised when a harness command produced no usable report.

    A gating boundary renders it as a DENIAL — a missing outcome is never an allow.
    `refusal` is the harness's own exit-plane message (`harness: ...` on stderr), if any.
    """

    def __init__(self, message: str, refusal: str | None = None) -> None:
        super().__init__(message)
        self.refusal = refusal


class CommandRunner(Protocol):
    def run_function(self, function: str, inquiry: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run one function command with its inquiry and answer its report."""


ProcessRunner = Callable[[Sequence[str], Mapping[str, str], "float | None"], "tuple[int, str, str]"]


def run_subprocess(
    argv: Sequence[str], environment: Mapping[str, str], timeout: float | None = None
) -> tuple[int, str, str]:
    """Run one command; past `timeout` seconds it is killed (`subprocess.TimeoutExpired`)."""
    completed = subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=dict(environment),
        check=False,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr


class SubprocessCommandRunner:
    """Invoke `harness.py <function>` as its own process, one command per function.

    `deadline` (a `clock` instant) bounds the WHOLE hook firing: the runtime treats a timed
    out `preToolUse` as an allow, so every command gets only the time left before it, and a
    command that would start or run past it raises `HarnessCommandError` — which a gating
    boundary renders as a denial while the host can still honor it.
    """

    def __init__(
        self,
        harness_entrypoint: Path,
        layout_environment: Mapping[str, str],
        run_process: ProcessRunner | None = None,
        python_executable: str | None = None,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._harness_entrypoint = harness_entrypoint
        self._layout_environment = dict(layout_environment)
        self._run_process = run_process or run_subprocess
        self._python_executable = python_executable or sys.executable
        self._deadline = deadline
        self._clock = clock

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
        timeout = None
        if self._deadline is not None:
            timeout = self._deadline - self._clock()
            if timeout <= 0:
                raise HarnessCommandError(
                    f"harness command '{function}' was not run: the hook deadline has passed"
                )
        try:
            exit_code, stdout, stderr = self._run_process(argv, environment, timeout)
        except subprocess.TimeoutExpired as failure:
            raise HarnessCommandError(
                f"harness command '{function}' did not answer before the hook deadline "
                f"({timeout:.1f}s left) and was stopped"
            ) from failure
        if exit_code != 0:
            raise HarnessCommandError(
                f"harness command '{function}' failed (exit {exit_code}): {stderr.strip()}",
                refusal=_read_refusal(stderr),
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


def _read_refusal(stderr: str) -> str | None:
    """The harness's own refusal line — never a traceback or any other raw output."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if lines and lines[-1].startswith("harness: "):
        return lines[-1][:2000]
    return None


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
