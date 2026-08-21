"""H4 `PreToolUse` (harness-command class) end to end — mediated session attribution."""

from __future__ import annotations

from typing import Callable

from harness_stub import HarnessStub
from hook_dispatch import HookRun
from host_events import TURN_SESSION_ID, pre_tool_use


def _terminal_call(command: str) -> dict[str, object]:
    return pre_tool_use("run_in_terminal", {"command": command, "isBackground": False})


class TestMediatedAttributionHook:
    """Adapter spec H4 — mediated attribution for functions 3-4, the agent-invoked pair."""

    def test_stamps_the_resolved_session_onto_the_whole_rewritten_tool_input(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H4, Mechanics rule 4: inject `--session-id <resolved current
        session>` via `updatedInput` — the FULL rewritten `tool_input`, never a patch (the
        host validates it against the tool's input schema). The hook invokes no function
        of its own: it is attribution plumbing (invariant 3).
        """
        open_turn()
        harness_stub.forget_calls()

        run = dispatch(
            "PreToolUse", _terminal_call("harness.py resolve-step --workflow verification")
        )

        assert run.exit_code == 0
        rendered = run.decision()["hookSpecificOutput"]
        assert rendered["updatedInput"] == {
            "command": (
                f"harness.py resolve-step --workflow verification "
                f"--session-id {TURN_SESSION_ID}"
            ),
            "isBackground": False,
        }
        assert harness_stub.calls == ()

    def test_denies_a_harness_function_that_is_not_agent_invocable(
        self, open_turn: Callable[[], HookRun], dispatch: Callable[..., HookRun]
    ) -> None:
        """Adapter spec H4, Mechanics rule 1: deny a harness invocation whose function is
        not `resolve-step` or `resolve-step-model` — every other function belongs to a hook
        boundary, and a model-authored call to one is never legitimate.
        """
        open_turn()

        run = dispatch("PreToolUse", _terminal_call("python3 harness.py end-session"))

        assert run.exit_code == 0
        rendered = run.decision()["hookSpecificOutput"]
        assert rendered["permissionDecision"] == "deny"
        assert "end-session" in rendered["permissionDecisionReason"]

    def test_denies_a_command_carrying_model_authored_session_attribution(
        self, open_turn: Callable[[], HookRun], dispatch: Callable[..., HookRun]
    ) -> None:
        """Adapter spec H4, Mechanics rule 2 / invariant 1: an invocation already carrying
        `--session-id` or `--parent-session-id` is DENIED outright, never merely
        overwritten — model-authored attribution is never accepted.
        """
        open_turn()

        run = dispatch(
            "PreToolUse",
            _terminal_call("harness.py resolve-step --workflow v --session-id forged-id"),
        )

        assert run.exit_code == 0
        rendered = run.decision()["hookSpecificOutput"]
        assert rendered["permissionDecision"] == "deny"
        assert "--session-id" in rendered["permissionDecisionReason"]

    def test_denies_a_harness_command_from_a_never_registered_session(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H4, Mechanics rule 3 / invariant 4: deny when the tracker resolves
        no current session — there is no session to attribute the call to, and a
        never-registered session is never stamped.
        """
        run = dispatch(
            "PreToolUse", _terminal_call("harness.py resolve-step --workflow verification")
        )

        assert run.exit_code == 0
        assert run.decision()["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert harness_stub.calls == ()

    def test_denies_a_shell_command_naming_a_governed_workspace_path(
        self, open_turn: Callable[[], HookRun], dispatch: Callable[..., HookRun]
    ) -> None:
        """Adapter spec H4, Mechanics rule 1 fall-through / I9: a non-harness command on a
        `guardedShellTools` tool that textually references a workspace artifact-layout path
        is denied with the offending path named — the advisory guard closing the routine
        write escape hatch.
        """
        open_turn()

        run = dispatch("PreToolUse", _terminal_call("rm workspace/portfolio/first.md"))

        assert run.exit_code == 0
        rendered = run.decision()["hookSpecificOutput"]
        assert rendered["permissionDecision"] == "deny"
        assert "workspace/portfolio/first.md" in rendered["permissionDecisionReason"]

    def test_an_ordinary_shell_command_passes_through_untouched(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H4, Mechanics rule 1 fall-through: a command that is neither a
        harness invocation nor a reference to a governed path passes through — exit 0,
        empty output, nothing stamped and nothing invoked.
        """
        open_turn()
        harness_stub.forget_calls()

        run = dispatch("PreToolUse", _terminal_call("python3 -m pytest tests -q"))

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()
