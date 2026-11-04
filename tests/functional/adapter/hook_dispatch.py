"""The real process boundary: one `dispatch.sh` invocation, in and out.

This is what separates this suite from the unit one. Nothing here builds an `Adapter`,
imports `adapter.py`, or calls a renderer: a hook run is a child process reading host JSON
on stdin and answering host JSON on stdout with an exit code — exactly the four seams of
the adapter spec's "Invocation plumbing and contract layering".
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from contract_assertions import assert_stdin_matches_contract, assert_stdout_matches_contract
from posix_shell import child_environment, shell_argv

ADAPTER_ENV = "vscode-github-copilot-chat"


@dataclass(frozen=True)
class HookRun:
    """One completed hook firing, as the host observes it."""

    exit_code: int
    stdout: str
    stderr: str

    def decision(self) -> dict[str, Any]:
        """Answer the host decision, validated against the seam-4 stdout contract."""
        return assert_stdout_matches_contract(self.stdout)

    @property
    def is_silent(self) -> bool:
        """Tell whether this firing answered nothing at all (the pass-through shape)."""
        return self.stdout == ""


def run_hook(
    dispatch_path: Path,
    event: str,
    payload: Mapping[str, Any],
    environment: Mapping[str, str],
    agent: str | None = None,
    cwd: Path | None = None,
) -> HookRun:
    """Fire one host event through `dispatch.sh` as the host's hook engine would.

    `dispatch.sh <event> <env> [<agent>]` — the third argument is the H0 scoping agent and
    is omitted entirely when there is none, exactly as the two rendered registrations do.
    """
    assert_stdin_matches_contract(payload)
    arguments = [event, ADAPTER_ENV] + ([agent] if agent is not None else [])
    completed = subprocess.run(
        shell_argv(dispatch_path, *arguments),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=child_environment(environment),
        cwd=str(cwd) if cwd is not None else None,
    )
    return HookRun(
        exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr
    )


__all__ = ["ADAPTER_ENV", "HookRun", "run_hook"]
