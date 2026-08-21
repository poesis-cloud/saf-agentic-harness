"""H1 `SubagentStart` end to end — the step session opens under its dispatcher."""

from __future__ import annotations

from typing import Callable

from harness_stub import HarnessStub, build_report
from hook_dispatch import HookRun
from host_events import (
    STEP_ACTOR,
    STEP_AGENT_ID,
    TURN_SESSION_ID,
    subagent_start,
)
from stub_framework import INSTRUCTION_BODY, SKILL_ID


class TestStepStartedHook:
    """Adapter spec H1 — step-started (functions 0, 6, 7)."""

    def test_registers_the_step_under_the_dispatching_turn_session(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H1, Harness invocations 1-3: registration first, with `agent_type`
        as the actor, `sanitized(agent_id)` as the step session id, and the dispatching
        turn session — resolved from the adapter's own tracker — as its parent.
        """
        open_turn()

        run = dispatch("SubagentStart", subagent_start())

        assert run.exit_code == 0
        assert harness_stub.functions[-3:] == (
            "start-session",
            "resolve-step-instructions",
            "resolve-step-skills",
        )
        registration = harness_stub.find_calls("start-session")[-1]
        assert registration.value("--agent") == STEP_ACTOR
        assert registration.value("--session-id") == STEP_AGENT_ID
        assert registration.value("--parent-session-id") == TURN_SESSION_ID

    def test_injects_the_step_context_on_the_subagent_start_decision_shape(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
    ) -> None:
        """Adapter spec H1, Output construction: one `additionalContext` string — inlined
        instruction content then skill load directives — under `hookEventName:
        SubagentStart` (a mismatched name would have the host strip the output).
        """
        open_turn()

        run = dispatch("SubagentStart", subagent_start())

        assert run.exit_code == 0
        injected = run.decision()["hookSpecificOutput"]
        assert injected["hookEventName"] == "SubagentStart"
        assert INSTRUCTION_BODY in injected["additionalContext"]
        assert f"skills/{SKILL_ID}.skill.md" in injected["additionalContext"]

    def test_a_failing_harness_reports_a_system_message_and_never_blocks_the_subagent(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H1 (as H0, Output construction): this boundary can only inject, so
        a harness error exits 0 with a `systemMessage` — never exit 2, never a decision.
        """
        open_turn()
        harness_stub.fails("resolve-step-skills", "skill catalog unreadable")

        run = dispatch("SubagentStart", subagent_start())

        assert run.exit_code == 0
        decision = run.decision()
        assert "skill catalog unreadable" in decision["systemMessage"]
        assert "decision" not in decision

    def test_a_context_ref_that_cannot_be_rendered_is_reported_not_crashed(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H1 (as H0, Output construction): a step instruction ref the adapter
        cannot resolve against `FRAMEWORK_INSTRUCTIONS_DIR` is reported as a
        `systemMessage` on exit 0 — this boundary can only inject, so it must still answer
        the host rather than die and answer nothing.
        """
        open_turn()
        harness_stub.answers(
            "resolve-step-instructions",
            build_report(
                "resolve-step-instructions", "resolved", instructions=["vanished-ref"]
            ),
        )

        run = dispatch("SubagentStart", subagent_start())

        assert run.exit_code == 0
        assert "vanished-ref" in run.decision()["systemMessage"]

    def test_a_foreign_subagent_passes_through_without_registering_anything(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H1, Preconditions / C7: a `SubagentStart` with no correlatable
        unresolved `step-resolution` entry gets `not-applicable` from function 0 — rendered
        as pass-through (exit 0, empty output), and no context is resolved for it.
        """
        open_turn()
        harness_stub.answers(
            "start-session", build_report("start-session", "not-applicable")
        )

        run = dispatch("SubagentStart", subagent_start(agent_type="foreign-agent"))

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.functions[-1] == "start-session"
        assert "resolve-step-instructions" not in harness_stub.functions

    def test_a_subagent_of_an_unregistered_conversation_reaches_no_harness_function(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec — correlation scenario 8 (C7): a firing for a `session_id` the
        tracker never registered resolves to no session at all, so nothing is invoked and
        nothing is journaled.
        """
        run = dispatch("SubagentStart", subagent_start())

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()
