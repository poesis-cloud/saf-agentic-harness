"""The ONE harness command line an agent may run, and how to recognize it (H4).

The adapter knows the interpreter it runs under (the venv the installer recorded) and the
harness entry point next to it, so it can both TELL an orchestrator the exact line to use
(injected with its workflow context) and REFUSE any look-alike: a `harness.py` elsewhere
on disk is not the harness, whatever its name.

The line the adapter rewrites an accepted command into is what the runtime's PERMISSION
layer then checks, so it must stay approvable by a plain `<interpreter>:*` rule: ONE command,
no `$env:`/`VAR=` assignment prefix, and an UNQUOTED interpreter path. The layout therefore
travels in a content-addressed file the harness reads (`--env-file`), not in the command.
"""

from __future__ import annotations

import hashlib
import ntpath
import os
import posixpath
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from .paths import resolve_host_path

# Any interpreter spelling an agent may use — bare (`python`, `py -3`) or a path to one; what
# is pinned is the harness entry point, not the interpreter that runs it.
_INTERPRETER_NAME = re.compile(r"python(?:3(?:\.[0-9]+)?)?|py")
# A path both shells take as ONE plain word, so it needs no quotes: quoting the interpreter
# makes the rewritten line unapprovable by the host's permission rules. A `~` is literal
# except leading (home expansion), which is what lets an 8.3 short name (`RUNNER~1`) through.
_SHELL_SAFE_PATH = re.compile(r"[A-Za-z0-9_.:/\\][A-Za-z0-9_.:/\\~-]*")
ENV_FILE_OPTION = "--env-file"
LAYOUT_FILES_DIR = "layouts"


def needs_quoting(path: str) -> bool:
    """Whether `path` must be quoted to stay one shell word (spaces, `'`, `$`, `&`, ...)."""
    return _SHELL_SAFE_PATH.fullmatch(path) is None


def _short_path(path: str) -> str | None:
    """The Windows 8.3 short form of an EXISTING path, or None (no 8.3 names, not Windows)."""
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        size = kernel32.GetShortPathNameW(path, None, 0)
        if not size:
            return None
        buffer = ctypes.create_unicode_buffer(size)
        if not kernel32.GetShortPathNameW(path, buffer, size):
            return None
        return buffer.value
    except (AttributeError, OSError):
        return None


def shell_safe(path: str) -> str:
    """`path`, or on Windows its 8.3 short form when only that needs no quoting.

    A missing tail (a state directory not created yet) is kept as is under its shortened,
    existing ancestor. Answers `path` unchanged when no unquoted spelling exists.
    """
    if not needs_quoting(path) or os.name != "nt":
        return path
    existing, missing = Path(path), []
    while not existing.exists() and existing.parent != existing:
        missing.insert(0, existing.name)
        existing = existing.parent
    short = _short_path(str(existing))
    if short is None:
        return path
    candidate = str(Path(short, *missing))
    return path if needs_quoting(candidate) else candidate


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
    layout_dir: str = ""

    def _word(self, value: str, powershell: bool) -> str:
        value = shell_safe(value)
        # A backslash is a path separator to PowerShell but an escape to bash.
        if not needs_quoting(value) and (powershell or "\\" not in value):
            return value
        return _powershell_quote(value) if powershell else _posix_quote(value)

    def example(self, arguments: str) -> str:
        prefix = "& " if self.windows else ""
        return (
            f"{prefix}{self._word(self.python, self.windows)} "
            f"{self._word(self.harness, self.windows)} {arguments}"
        )

    def layout_text(self) -> str:
        return "".join(f"{name}={value}\n" for name, value in sorted(self.environment.items()))

    def layout_file(self) -> str:
        """Where the layout this adapter runs under is published for agent-run commands —
        named by its content, so concurrent hooks agree and a changed layout never reuses
        a stale file."""
        digest = hashlib.sha256(self.layout_text().encode("utf-8")).hexdigest()[:16]
        return str(Path(self.layout_dir) / f"layout-{digest}.env")

    def publish_layout(self) -> str:
        """Write the layout file if absent (atomically); return its path. Raises `OSError`
        or `ValueError` when it cannot be published — the caller then denies."""
        if not self.layout_dir or not self.environment:
            raise ValueError("no layout directory or layout environment is configured")
        for name, value in self.environment.items():
            if "\n" in value or "\r" in value or value != value.strip():
                raise ValueError(f"layout variable {name} cannot be written as one KEY=VALUE line")
        text = self.layout_text()
        target = Path(self.layout_file())
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            return str(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return str(target)

    def canonical(self, function: str, attribution: Sequence[str], flags: str, powershell: bool) -> str:
        """The adapter-authored command actually run, and checked by the host's permission
        layer: ONE plain invocation of the installed interpreter (unquoted when its path
        allows), the installed harness, the published layout file, the stamped session and
        the agent's own `flags` (already validated as plain `--flag [value]` tokens)."""
        layout = self.publish_layout()
        call = " ".join((function, *attribution)) + flags
        prefix = "& " if powershell else ""
        return (
            f"{prefix}{self._word(self.python, powershell)} {self._word(self.harness, powershell)} "
            f"{ENV_FILE_OPTION} {self._word(layout, powershell)} {call}"
        )

    def is_harness(self, raw: str, cwd: str | None) -> bool:
        resolved = resolve_host_path(raw, cwd)
        return resolved in {
            resolve_host_path(self.harness, None),
            resolve_host_path(shell_safe(self.harness), None),
        }

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
                "--session-id, --parent-session-id or --env-file — the adapter adds them itself:",
                "",
                f"    {self.example('resolve-step --workflow-slug <workflow-slug>')}",
                f"    {self.example('resolve-step-model')}",
                "",
                "The harness REWRITES an accepted line before it runs (it adds --env-file and "
                "--session-id). If that rewritten line is then denied, the denial comes from "
                "the host's permission layer, not from your command: do not retry or edit it; "
                "tell the user to approve the interpreter (see the harness adapter README).",
            )
        )
        return "\n".join(lines)


__all__ = [
    "ENV_FILE_OPTION",
    "LAYOUT_FILES_DIR",
    "HarnessInvocation",
    "needs_quoting",
    "shell_safe",
    "unquote",
]
