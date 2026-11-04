"""H3 host adaptation — the runtime's `create` refuses a missing parent folder.

Live trial (CLI 1.0.105 / app): `create` answered "Parent directory … does not exist. You need
to create it before creating the file", and `edit` needs an existing file. The freeform
`apply_patch` (whose `*** Add File:` does create parents) is offered only to GPT-5.x models.
So once the harness has ALLOWED a `create` of an artifact path and the whole firing is
allowed, the adapter creates the missing folders inside the workspace: no content, no commit.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from cli_rig import (
    Adapter,
    ContextRenderer,
    FakeRunner,
    Rig,
    SessionTracker,
    build_report,
    make_invocation,
)


def _rig(tmp_path: Path, binding, make_folders=None) -> tuple[Rig, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tracker, runner, diagnostics = SessionTracker(tmp_path / "state"), FakeRunner(), []
    options: dict[str, Any] = {} if make_folders is None else {"make_folders": make_folders}
    adapter = Adapter(
        binding=binding,
        tracker=tracker,
        context_renderer=ContextRenderer(tmp_path, tmp_path),
        runner=runner,
        workspace_dir=str(workspace),
        invocation=make_invocation(),
        diagnostic=diagnostics.append,
        **options,
    )
    rig = Rig(adapter, runner, tracker, binding, diagnostics)
    rig.open_turn()
    return rig, workspace


def _pre(cwd: Path, *calls: tuple[str, Any]) -> dict[str, Any]:
    return {
        "sessionId": "1d793d07-adc6-4f25-bec3-5b03b44e949e",
        "cwd": str(cwd),
        "toolCalls": [
            {"id": f"toolu_{index}", "name": name, "args": args}
            for index, (name, args) in enumerate(calls)
        ],
    }


def _create(path: Path | str) -> tuple[str, dict[str, str]]:
    return ("create", {"path": str(path), "file_text": "x"})


def test_an_allowed_create_gets_its_missing_folders_and_nothing_else(tmp_path, binding) -> None:
    rig, workspace = _rig(tmp_path, binding)
    target = workspace / "portfolio" / "products" / "acme" / "vision.md"
    assert rig.fire("preToolUse", _pre(workspace, _create(target))) is None
    (inquiry,) = rig.runner.inquiries("check-step-authorization")
    assert (inquiry["action"], inquiry["artifactPath"]) == (
        "create",
        "portfolio/products/acme/vision.md",
    )
    assert target.parent.is_dir()
    assert not target.exists()
    assert rig.runner.functions().count("check-step-artifact") == 0


def test_a_relative_create_target_resolves_against_the_cwd(tmp_path, binding) -> None:
    rig, workspace = _rig(tmp_path, binding)
    relative = os.path.join("portfolio", "epics", "e1", "case.md")
    assert rig.fire("preToolUse", _pre(workspace, _create(relative))) is None
    assert (workspace / "portfolio" / "epics" / "e1").is_dir()


def test_a_denied_create_gets_no_folder(tmp_path, binding) -> None:
    rig, workspace = _rig(tmp_path, binding)
    rig.runner.script["check-step-authorization"] = build_report(
        "check-step-authorization",
        "denied",
        authorization={
            "actor": "value-management-officer",
            "artifactPath": "portfolio/x/y.md",
            "action": "create",
            "resource": "epic",
            "failureMessage": "missing privilege: create epic",
        },
    )
    target = workspace / "portfolio" / "x" / "y.md"
    decision = rig.fire("preToolUse", _pre(workspace, _create(target)))
    assert decision is not None and decision["permissionDecision"] == "deny"
    assert not (workspace / "portfolio").exists()


def test_no_folder_is_created_when_another_call_of_the_firing_is_denied(
    tmp_path, binding
) -> None:
    rig, workspace = _rig(tmp_path, binding)
    target = workspace / "portfolio" / "x" / "y.md"
    patch = "*** Begin Patch\n*** Frobnicate: nothing\n*** End Patch\n"
    decision = rig.fire(
        "preToolUse", _pre(workspace, _create(target), ("apply_patch", patch))
    )
    assert decision is not None and decision["permissionDecision"] == "deny"
    assert not (workspace / "portfolio").exists()


def test_no_folder_is_created_outside_the_workspace(tmp_path, binding) -> None:
    rig, workspace = _rig(tmp_path, binding)
    outside = tmp_path / "elsewhere" / "deep" / "z.md"
    assert rig.fire("preToolUse", _pre(workspace, _create(outside))) is None
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.parametrize(
    "call",
    [
        ("apply_patch", "*** Begin Patch\n*** Add File: {target}\n+one\n*** End Patch\n"),
        ("edit", {"path": "{target}", "old_str": "a", "new_str": "b"}),
    ],
    ids=["apply_patch-add-file-creates-its-own-folders", "edit-needs-an-existing-file"],
)
def test_only_the_create_tool_gets_folders(tmp_path, binding, call) -> None:
    rig, workspace = _rig(tmp_path, binding)
    target = str(workspace / "portfolio" / "x" / "y.md")
    name, args = call
    args = (
        args.format(target=target)
        if isinstance(args, str)
        else {key: value.format(target=target) for key, value in args.items()}
    )
    assert rig.fire("preToolUse", _pre(workspace, (name, args))) is None
    assert not (workspace / "portfolio").exists()


def test_a_folder_failure_is_a_diagnostic_and_the_create_is_still_allowed(
    tmp_path, binding
) -> None:
    def refuse(path: str) -> None:
        raise PermissionError(f"denied: {path}")

    rig, workspace = _rig(tmp_path, binding, make_folders=refuse)
    target = workspace / "portfolio" / "x" / "y.md"
    assert rig.fire("preToolUse", _pre(workspace, _create(target))) is None
    assert any("could not create the folder" in line for line in rig.diagnostics)


@pytest.mark.skipif(os.name == "nt", reason="the rig's workspace is the local flavor on Windows")
def test_a_foreign_flavor_path_is_never_created_on_this_host(rig: Rig) -> None:
    from cli_rig import WORKSPACE, pre_tool_use

    rig.open_turn()
    write = {"path": WORKSPACE + r"\portfolio\x\y.md", "file_text": "x"}
    assert rig.fire("preToolUse", pre_tool_use(("create", write))) is None
    assert rig.folders == []
