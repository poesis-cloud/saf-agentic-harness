"""Session records are stamped (workspace, harness version); another version is refused.

Adapter spec, Session identity: each record is stamped with the governed workspace and the
harness version that opened it, when it is created (H0 or H1). Every later firing uses the
recorded workspace, and a firing whose installed version differs from the record's is
denied with "the plugin was updated; start a new session"; H7 still ends that session.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cli_rig import (
    HOST,
    SUBAGENT,
    WORKSPACE,
    FakeRunner,
    Rig,
    pre_tool_use,
)
from github_copilot_adapter.core import PLUGIN_UPDATED, Adapter
from github_copilot_adapter.payload import HookInput
from github_copilot_adapter.tracker import SessionTracker

VERSION = "1.0.0-alpha.2"


def _versioned(rig: Rig, version: str = VERSION, workspace: str = WORKSPACE) -> Rig:
    adapter = Adapter(
        binding=rig.binding,
        tracker=rig.tracker,
        context_renderer=rig.adapter._context,
        runner=rig.runner,
        workspace_dir=workspace,
        invocation=rig.adapter._invocation,
        diagnostic=rig.diagnostics.append,
        make_folders=lambda root, parts: rig.folders.append("/".join((root, *parts))),
        harness_version=version,
    )
    return replace(rig, adapter=adapter)


def test_h0_and_h1_stamp_the_workspace_and_the_harness_version(rig: Rig) -> None:
    rig = _versioned(rig)
    rig.open_turn()
    rig.open_step()

    for host in (HOST, SUBAGENT):
        record = rig.tracker.read(host)
        assert record is not None
        assert (record.workspace, record.harness_version) == (WORKSPACE, VERSION)


def test_a_session_another_version_opened_is_denied_at_pre_tool_use(rig: Rig) -> None:
    _versioned(rig, version="1.0.0-alpha.1").open_turn()
    rig = _versioned(rig)
    calls_before = len(rig.runner.calls)

    decision = rig.fire("preToolUse", pre_tool_use(("view", {"path": "x"})))

    assert decision is not None and decision["permissionDecision"] == "deny"
    assert PLUGIN_UPDATED in decision["permissionDecisionReason"]
    assert len(rig.runner.calls) == calls_before
    assert any("1.0.0-alpha.1" in line for line in rig.diagnostics)


def test_a_record_an_older_harness_wrote_counts_as_another_version(rig: Rig) -> None:
    rig.open_turn()  # the rig's own adapter stamps no version
    rig = _versioned(rig)

    decision = rig.fire("preToolUse", pre_tool_use(("view", {"path": "x"})))

    assert decision is not None and PLUGIN_UPDATED in decision["permissionDecisionReason"]


def test_closure_still_ends_a_session_another_version_opened(rig: Rig) -> None:
    """Core function 11 is exempt from the version check, so H7 runs."""
    _versioned(rig, version="1.0.0-alpha.1").open_turn()
    rig = _versioned(rig)

    rig.fire("agentStop", {"sessionId": HOST, "timestamp": 2})

    assert "end-session" in rig.runner.functions()
    assert rig.tracker.read(HOST) is None


def test_other_events_of_another_versions_session_run_nothing(rig: Rig) -> None:
    _versioned(rig, version="1.0.0-alpha.1").open_turn()
    rig = _versioned(rig)
    calls_before = len(rig.runner.calls)

    assert rig.fire("userPromptTransformed", {
        "sessionId": HOST, "prompt": "go", "transformedPrompt": "go", "timestamp": 3,
    }, "release-train-engineer") is None
    assert len(rig.runner.calls) == calls_before


def test_a_matching_version_is_not_refused(rig: Rig) -> None:
    rig = _versioned(rig)
    rig.open_turn()

    decision = rig.fire("preToolUse", pre_tool_use(("view", {"path": "x"})))

    assert decision is None or PLUGIN_UPDATED not in str(decision)


def test_the_recorded_workspace_wins_over_the_firings_own(
    rig: Rig, tmp_path: Path
) -> None:
    """A later firing never re-resolves the workspace; H1 takes the dispatcher's."""
    _versioned(rig, workspace="/fixed/workspace").open_turn()
    later = _versioned(rig, workspace="/elsewhere")

    def hook(event: str, payload: dict) -> HookInput:
        return HookInput(event=event, payload=payload, binding=rig.binding)

    assert later.adapter.recorded_workspace(
        hook("preToolUse", pre_tool_use(("view", {"path": "x"})))
    ) == "/fixed/workspace"
    assert later.adapter.recorded_workspace(
        hook("subagentStart", {
            "sessionId": "nested-id", "parentAgentId": HOST, "agentId": SUBAGENT,
            "agentType": "x",
        })
    ) == "/fixed/workspace"
    assert later.adapter.recorded_workspace(
        hook("preToolUse", pre_tool_use(("view", {"path": "x"}), host="unknown"))
    ) is None


def test_the_entry_rebuilds_the_adapter_on_the_recorded_workspace(tmp_path: Path) -> None:
    """`run_hook` re-pins the firing to its session's recorded workspace."""
    import io

    from cli_rig import ADAPTER_DIR, load_script, make_invocation
    from github_copilot_adapter.binding import load_tool_binding
    from github_copilot_adapter.renderer import ContextRenderer

    entry = load_script("github_copilot_adapter_entry_pin", "adapter.py")
    binding = load_tool_binding(ADAPTER_DIR)
    tracker = SessionTracker(tmp_path / "state")
    from github_copilot_adapter.tracker import TURN_ORIGIN, SessionRecord

    tracker.write(HOST, SessionRecord(
        "turn", "rte", TURN_ORIGIN, workspace="/fixed/workspace", harness_version=VERSION,
    ))
    built: list[str | None] = []

    def build(framework_dir, workspace_dir, overrides, deadline):
        built.append(workspace_dir)
        adapter = Adapter(
            binding=binding,
            tracker=tracker,
            context_renderer=ContextRenderer(tmp_path, tmp_path),
            runner=FakeRunner(),
            workspace_dir=workspace_dir or "/from/arguments",
            invocation=make_invocation(),
            diagnostic=lambda _: None,
            harness_version=VERSION,
        )
        return adapter, binding

    payload = b'{"sessionId": "%s", "cwd": "/x", "toolCalls": []}' % HOST.encode()
    code = entry.run_hook(
        ["hook", "--event", "preToolUse", "--workspace-dir", "/from/arguments"],
        io.BytesIO(payload), io.StringIO(), io.StringIO(), build,
    )

    assert code == 0
    assert built == ["/from/arguments", "/fixed/workspace"]


def test_a_nested_h1_is_refused_on_its_dispatchers_version(rig: Rig) -> None:
    """A nested `subagentStart` carries an unrelated `sessionId`: the version check reads
    the dispatcher (`parentAgentId`), as the workspace lookup does."""
    _versioned(rig, version="1.0.0-alpha.1").open_turn()
    rig = _versioned(rig)
    calls_before = len(rig.runner.calls)

    decision = rig.fire("subagentStart", {
        "sessionId": "unrelated-nested-id", "parentAgentId": HOST, "agentId": SUBAGENT,
        "agentType": "builder", "agentName": "builder", "timestamp": 2,
    })

    assert decision is None
    assert len(rig.runner.calls) == calls_before
    assert rig.tracker.read(SUBAGENT) is None
