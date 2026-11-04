"""Run the harness's POSIX shell entries (`dispatch.sh`, fake interpreters) on every OS.

The VS Code adapter's host entry is a bash script (its README lists `bash` as a
prerequisite). POSIX hosts exec it directly through its shebang; Windows cannot exec a
shebang script, so there the suite runs it through Git for Windows' bash — the one a
Windows VS Code user installs — and skips when no such bash is present. WSL's
`System32\\bash.exe` is never used: it runs a different filesystem and interpreter.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping, Sequence

import pytest

IS_WINDOWS = os.name == "nt"

# A Windows child process needs these to start at all (Python's own startup reads
# SYSTEMROOT); an environment built from scratch inherits them from the test process.
_WINDOWS_ESSENTIALS = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")


def find_git_bash() -> str | None:
    """Answer a bash able to run the shell entries, or None when there is none."""
    if not IS_WINDOWS:
        return shutil.which("bash")
    located = shutil.which("bash")
    if located and "\\windows\\" not in located.lower() and "windowsapps" not in located.lower():
        return located
    for root in (os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles"), r"C:\Program Files"):
        if root:
            candidate = Path(root) / "Git" / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def shell_argv(script: Path | str, *arguments: str) -> list[str]:
    """Answer the argv running one shell script, through bash on Windows."""
    if not IS_WINDOWS:
        return [str(script), *arguments]
    bash = find_git_bash()
    if bash is None:
        pytest.skip("Git for Windows bash is required to run the POSIX shell entries")
    return [bash, str(script), *arguments]


def child_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Answer a from-scratch child environment, plus what Windows needs to start a child."""
    merged = dict(environment)
    if IS_WINDOWS:
        for name in _WINDOWS_ESSENTIALS:
            if name not in merged and name in os.environ:
                merged[name] = os.environ[name]
    return merged


def write_fake_interpreter(path: Path, body: Sequence[str] | str) -> Path:
    """Write an executable POSIX `sh` script standing in for an interpreter."""
    lines = [body] if isinstance(body, str) else list(body)
    path.write_text("#!/bin/sh\n" + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def write_failing_native_interpreter(directory: Path) -> Path:
    """Write an interpreter a NATIVE process launch can run, that exits 1.

    A Python `subprocess` on Windows cannot exec a shebang script, so a stand-in that
    must be *launched* and then *fail* (rather than fail to launch) is a `.bat` there.
    """
    if IS_WINDOWS:
        fake = directory / "bare-python.bat"
        fake.write_text("@exit /b 1\r\n", encoding="utf-8")
        return fake
    return write_fake_interpreter(directory / "bare-python", "exit 1")


__all__ = [
    "IS_WINDOWS",
    "child_environment",
    "find_git_bash",
    "shell_argv",
    "write_failing_native_interpreter",
    "write_fake_interpreter",
]
