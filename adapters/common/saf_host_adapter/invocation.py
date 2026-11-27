"""The ONE harness request an agent may issue, and how to recognize it (H4).

The adapter knows the interpreter it runs under (the venv the installer recorded) and the
harness entry point next to it, so it can both TELL an orchestrator the exact line to use
(injected with its workflow context) and REFUSE any look-alike: a `harness.py` elsewhere
on disk is not the harness, whatever its name.

The line is a REQUEST, never executed by the agent's shell: the `preToolUse` hook runs the
harness function itself, attributed to the session it observed, and returns the report.
"""

from __future__ import annotations

import ntpath
import posixpath
import re
from dataclasses import dataclass

from .paths import resolve_host_path

# Any interpreter spelling an agent may use — bare (`python`, `py -3`) or a path to one; what
# is pinned is the harness entry point, not the interpreter that runs it.
_INTERPRETER_NAME = re.compile(r"python(?:3(?:\.[0-9]+)?)?|py")
# A path the request pattern reads as ONE token without quotes.
_NEEDS_QUOTES = re.compile(r"""[\s'";|&`$()<>{}]""")


# Characters never accepted inside a quoted token, even where the shell reads them literally.
_UNSAFE_IN_QUOTES = "`$;|&<>"
_POSIX_APOSTROPHE = "'\"'\"'"
_DOUBLE_QUOTED = rf'"[^"{_UNSAFE_IN_QUOTES}]*"'
_SINGLE_BODY = rf"""[^'{_UNSAFE_IN_QUOTES}]*"""


def quoted_pattern(powershell: bool) -> str:
    """The quoted-token forms accepted for the shell, matching what `_quote` renders.

    PowerShell: `'...'` with an apostrophe doubled (`'o''neil'`). POSIX: `'...'`, an
    apostrophe rendered as the `'"'"'` concatenation (`'o'"'"'neil'`). Both: `"..."`.
    """
    if powershell:
        single = rf"""'(?:{_SINGLE_BODY}(?:''{_SINGLE_BODY})*)'"""
    else:
        single = rf"""'{_SINGLE_BODY}'(?:"'"'{_SINGLE_BODY}')*"""
    return rf"""(?:{single}|{_DOUBLE_QUOTED})"""


def unquote(token: str, powershell: bool = False) -> str:
    """Decode one token matched by `quoted_pattern` (or a plain token) to its value."""
    if len(token) < 2 or token[0] not in "'\"":
        return token
    if token[0] == '"':
        return token[1:-1]
    if powershell:
        return token[1:-1].replace("''", "'")
    return token[1:-1].replace(_POSIX_APOSTROPHE, "'")


def _quote(value: str, powershell: bool) -> str:
    if _NEEDS_QUOTES.search(value) is None:
        return value
    if powershell:
        return "'" + value.replace("'", "''") + "'"
    return "'" + value.replace("'", _POSIX_APOSTROPHE) + "'"


@dataclass(frozen=True)
class HarnessInvocation:
    python: str
    harness: str
    windows: bool
    framework_dir: str = ""
    workspace_dir: str = ""

    def example(self, arguments: str) -> str:
        prefix = "& " if self.windows else ""
        return (
            f"{prefix}{_quote(self.python, self.windows)} "
            f"{_quote(self.harness, self.windows)} {arguments}"
        )

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
                "Ask the harness with the shell tool, as ONE plain command per tool call "
                "(never chained, piped, redirected or batched with other tool calls), and "
                "never pass --session-id or --parent-session-id:",
                "",
                f"    {self.example('resolve-step --workflow-slug <workflow-slug>')}",
                f"    {self.example('resolve-step-model')}",
                "",
                "Your shell does NOT run this command. The harness hook runs it for your "
                "session and returns its JSON report as the tool response; the host displays "
                "that response as a hook denial whose reason starts with "
                "'SAFe agentic harness RESULT'. That is the successful answer, not an error: "
                "act on the report and do not re-issue the command to get it again. Only a "
                "reason starting with 'SAFe agentic harness REJECTED' means the request itself "
                "was refused; fix what it names.",
            )
        )
        return "\n".join(lines)


__all__ = ["HarnessInvocation", "quoted_pattern", "unquote"]
