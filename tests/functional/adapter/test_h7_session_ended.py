"""H7 `SubagentStop` / `Stop` end to end — best-effort closure, silent either way."""

from __future__ import annotations

from typing import Callable

from harness_stub import HarnessStub
from hook_dispatch import HookRun
from host_events import (
    STEP_AGENT_ID,
    TURN_SESSION_ID,
    dispatch_tool_input,
    pre_tool_use,
    stop,
    subagent_start,
    subagent_stop,
)


class TestSessionEndedHook:
    """Adapter spec H7 — session-ended (function 11), no host-visible effect either way."""

    def test_subagent_stop_closes_the_step_session_and_says_nothing_to_the_host(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H7, Harness invocation 1 / Out: on `SubagentStop` the ending
        session is `sanitized(agent_id)` directly, and the hook answers exit 0 with EMPTY
        stdout — never `decision: block`, which would force the agent to continue.
        """
        open_turn()
        dispatch("SubagentStart", subagent_start())
        harness_stub.forget_calls()

        run = dispatch("SubagentStop", subagent_stop())

        assert run.exit_code == 0
        assert run.is_silent
        closure = harness_stub.find_calls("end-session")
        assert len(closure) == 1
        assert closure[0].value("--session-id") == STEP_AGENT_ID

    def test_subagent_stop_pops_the_step_so_later_firings_resolve_to_the_dispatcher(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
    ) -> None:
        """Adapter spec H7, Harness invocation 1: the adapter pops the tracker stack,
        restoring the dispatching orchestrator session — without this, the orchestrator's
        next mediated call (H4) would misattribute to the now-closed step session.
        """
        open_turn()
        dispatch("SubagentStart", subagent_start())
        dispatch("SubagentStop", subagent_stop())

        run = dispatch(
            "PreToolUse",
            pre_tool_use(
                "run_in_terminal",
                {"command": "harness.py resolve-step --workflow verification"},
            ),
        )

        assert run.exit_code == 0
        stamped = run.decision()["hookSpecificOutput"]["updatedInput"]["command"]
        assert stamped.endswith(f"--session-id {TURN_SESSION_ID}")

    def test_stop_closes_the_resolved_turn_session_never_the_raw_conversation_id(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H7, Harness invocation 2: `Stop` resolves the current session
        FIRST and closes THAT id — never the raw `session_id`, which the conversation may
        still reuse for later agent sessions.
        """
        open_turn()
        harness_stub.forget_calls()

        run = dispatch("Stop", stop())

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.find_calls("end-session")[0].value("--session-id") == (
            TURN_SESSION_ID
        )

    def test_stop_clears_the_stack_so_a_later_firing_passes_through(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H7, Harness invocation 2: the emptied tracker makes any later
        firing in this conversation resolve to `None` — the correct C7 pass-through
        instead of a stale framework session.
        """
        open_turn()
        dispatch("Stop", stop())
        harness_stub.forget_calls()

        run = dispatch("PreToolUse", pre_tool_use("runSubagent", dispatch_tool_input()))

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()

    def test_a_failing_closure_stays_silent_and_still_exits_zero(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H7, Out: `end-session`'s outcome is never surfaced to the host,
        success or error alike — a failed closure is an operational diagnostic (stderr),
        never a host decision and never a non-zero exit.
        """
        open_turn()
        harness_stub.fails("end-session", "session log unwritable")

        run = dispatch("Stop", stop())

        assert run.exit_code == 0
        assert run.is_silent
        assert "session log unwritable" in run.stderr

    def test_a_stop_of_an_unregistered_conversation_invokes_no_closure(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H7, Harness invocation: if resolution finds no current session
        (never registered), no harness function is invoked — pass-through, as elsewhere in
        this binding.
        """
        run = dispatch("Stop", stop())

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()
