"""I9 — the guarded shell names the workspace by RELATIVE path too.

Live trial: step actors ran in the workspace (`cwd` = the workspace) and neither
`New-Item … portfolio\\…` nor `git show HEAD:<path>` was denied, because the guard only
matched the workspace's name or its absolute prefix. The spec rule is unchanged: a shell
command naming the workspace "by name, relative path or absolute prefix" is denied, read-only
commands included.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from cli_rig import (
    CWD,
    WORKSPACE,
    Adapter,
    ContextRenderer,
    FakeRunner,
    Rig,
    SessionTracker,
    make_invocation,
    pre_tool_use,
)


def _shell(command: str, cwd: str) -> dict[str, Any]:
    return {**pre_tool_use(("powershell", {"command": command})), "cwd": cwd}


def _reason(rig: Rig, command: str, cwd: str) -> str | None:
    decision = rig.fire("preToolUse", _shell(command, cwd))
    if decision is None:
        return None
    assert decision["permissionDecision"] == "deny"
    return decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        r"New-Item -ItemType Directory -Force -Path portfolio\products\acme",
        "New-Item -ItemType Directory portfolio/products",
        "git show HEAD:portfolio/portfolio-manifest.yaml",
        "git show HEAD:./portfolio/portfolio-manifest.yaml",
        "git diff HEAD -- portfolio",
        "git checkout -- portfolio-manifest.yaml",
        r"Get-Content .\portfolio\portfolio-manifest.yaml",
        "git add .",
        "Out-File --FilePath=portfolio/x.md",
    ],
)
def test_a_relative_workspace_path_from_inside_the_workspace_is_denied(
    rig: Rig, command: str
) -> None:
    rig.open_turn()
    reason = _reason(rig, command, WORKSPACE)
    assert reason is not None and "governed workspace path" in reason


@pytest.mark.parametrize(
    "command",
    [
        "git status --short",
        "git log --oneline -5",
        r"Get-Content ..\README.md",
        "Invoke-WebRequest https://example.com/portfolio/x.md",
        r"Get-ChildItem $env:TEMP\portfolio",
        "Write-Output done",
    ],
)
def test_commands_naming_no_workspace_path_still_pass_from_inside_the_workspace(
    rig: Rig, command: str
) -> None:
    rig.open_turn()
    assert _reason(rig, command, WORKSPACE) is None


def test_relative_paths_resolve_against_the_events_cwd(rig: Rig) -> None:
    """From the framework root, `portfolio/…` is not in the workspace; `workspace/…` is."""
    rig.open_turn()
    assert _reason(rig, "git show HEAD:portfolio/x.md", CWD) is None
    assert _reason(rig, r"Get-Content adapters\README.md", CWD) is None
    assert _reason(rig, "git show HEAD:workspace/portfolio/x.md", CWD) is not None
    assert _reason(rig, r"Get-Content .\workspace\portfolio\x.md", CWD) is not None


def test_a_bare_name_that_exists_in_the_workspace_is_denied(tmp_path: Path, binding) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "portfolio").mkdir(parents=True)
    tracker, runner = SessionTracker(tmp_path / "state"), FakeRunner()
    adapter = Adapter(
        binding=binding,
        tracker=tracker,
        context_renderer=ContextRenderer(tmp_path, tmp_path),
        runner=runner,
        workspace_dir=str(workspace),
        invocation=make_invocation(),
        diagnostic=lambda _: None,
    )
    rig = Rig(adapter, runner, tracker, binding, [])
    rig.open_turn()
    assert _reason(rig, "Remove-Item -Recurse portfolio", str(workspace)) is not None
    assert _reason(rig, "Remove-Item -Recurse scratch", str(workspace)) is None


@pytest.mark.parametrize(
    "command",
    [
        'curl --output="workspace/portfolio/x.md" https://example.com/x',
        "curl --output='workspace/portfolio/x.md' https://example.com/x",
        'Out-File -FilePath="workspace\\portfolio\\x.md"',
    ],
)
def test_a_quoted_option_value_naming_the_workspace_is_denied(rig: Rig, command: str) -> None:
    """The token trim removes only the closing quote of `--opt="value"`; the value is
    checked without its quotes."""
    rig.open_turn()
    reason = _reason(rig, command, CWD)
    assert reason is not None and "governed workspace path" in reason


@pytest.mark.parametrize(
    "command",
    [
        "git log --format=%h:%s -5",
        "git log --pretty=format:%h:%s",
        "git for-each-ref --format=%(refname):%(objectname)",
    ],
)
def test_an_option_token_is_never_a_revision_path_reference(rig: Rig, command: str) -> None:
    """`--format=%h:%s` names no `<rev>:<path>` object, even from inside the workspace."""
    rig.open_turn()
    assert _reason(rig, command, WORKSPACE) is None
