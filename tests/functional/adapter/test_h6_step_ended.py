"""H6 `PostToolUse` (dispatch class) end to end — THE step evaluation point."""

from __future__ import annotations

from typing import Callable

from harness_stub import (
    HarnessStub,
    build_failing_condition_check,
    build_report,
)
from hook_dispatch import HookRun
from host_events import (
    TURN_SESSION_ID,
    dispatch_tool_input,
    post_tool_use,
    subagent_start,
)


def _dispatch_return() -> dict[str, object]:
    return post_tool_use("runSubagent", dispatch_tool_input())


class TestStepEndedHook:
    """Adapter spec H6 — step-ended (function 10), evaluated once per step pass."""

    def test_evaluates_in_the_dispatching_session_even_while_a_step_is_stacked(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H6, Harness invocation: function 10 runs in the DISPATCHING
        (orchestrator) session — the adapter defensively resolves the stack BASE, which is
        correct under either ordering of `SubagentStop` and this `PostToolUse`.
        """
        open_turn()
        dispatch("SubagentStart", subagent_start())

        run = dispatch("PostToolUse", _dispatch_return())

        assert run.exit_code == 0
        evaluation = harness_stub.find_calls("check-step-postconditions")[0]
        assert evaluation.value("--session-id") == TURN_SESSION_ID

    def test_passing_postconditions_answer_plain_success(
        self, open_turn: Callable[[], HookRun], dispatch: Callable[..., HookRun]
    ) -> None:
        """Adapter spec H6, Output construction: `pass` renders plain success — the
        orchestrator proceeds to the next `resolve-step` per its own instructions.
        """
        open_turn()

        run = dispatch("PostToolUse", _dispatch_return())

        assert run.exit_code == 0
        assert run.decision() == {"continue": True}

    def test_failing_postconditions_block_the_orchestrator_into_re_resolution(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H6, Output construction / invariant 2: `fail` renders
        `decision: block` serializing the `conditionChecks[]`, with `additionalContext`
        restating the `reports-handling` reaction — the reason addresses the orchestrator,
        never the user.
        """
        open_turn()
        harness_stub.answers(
            "check-step-postconditions",
            build_report(
                "check-step-postconditions",
                "fail",
                conditionChecks=[
                    build_failing_condition_check(
                        "report-exists", "no artifact matches 'review-report'"
                    )
                ],
            ),
        )

        run = dispatch("PostToolUse", _dispatch_return())

        assert run.exit_code == 0
        decision = run.decision()
        assert decision["decision"] == "block"
        assert "report-exists" in decision["reason"]
        assert "resolve-step" in decision["hookSpecificOutput"]["additionalContext"]

    def test_an_unavailable_harness_blocks_rather_than_letting_the_step_pass(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H6, Output construction: harness errors block with the error
        detail — an unevaluated step is never reported as a passing one. Exit stays 0.
        """
        open_turn()
        harness_stub.fails("check-step-postconditions", "condition evaluator crashed")

        run = dispatch("PostToolUse", _dispatch_return())

        assert run.exit_code == 0
        assert run.decision()["decision"] == "block"
        assert "condition evaluator crashed" in run.decision()["reason"]

    def test_a_dispatch_return_with_no_in_flight_step_passes_through(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H6, Harness invocation: a target with no matching in-flight step is
        already `not-applicable`, rendered as pass-through — exit 0, empty output.
        """
        open_turn()
        harness_stub.answers(
            "check-step-postconditions",
            build_report("check-step-postconditions", "not-applicable"),
        )

        run = dispatch("PostToolUse", _dispatch_return())

        assert run.exit_code == 0
        assert run.is_silent
