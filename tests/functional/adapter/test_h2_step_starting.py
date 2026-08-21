"""H2 `PreToolUse` (dispatch class) end to end — step preconditions gate the dispatch."""

from __future__ import annotations

from typing import Callable

from harness_stub import (
    HarnessStub,
    build_failing_condition_check,
    build_report,
)
from hook_dispatch import HookRun
from host_events import TURN_SESSION_ID, dispatch_tool_input, pre_tool_use


def _dispatch_call() -> dict[str, object]:
    return pre_tool_use("runSubagent", dispatch_tool_input())


class TestStepStartingHook:
    """Adapter spec H2 — step-starting (function 5), THE precondition enforcement point."""

    def test_passing_preconditions_allow_the_dispatch_in_the_turn_session(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H2, Output construction: `pass → allow`, invoked against the
        current agent session of this `session_id` (the orchestrator's turn session).
        """
        open_turn()

        run = dispatch("PreToolUse", _dispatch_call())

        assert run.exit_code == 0
        assert run.decision()["hookSpecificOutput"] == {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
        gate = harness_stub.find_calls("check-step-preconditions")[0]
        assert gate.value("--session-id") == TURN_SESSION_ID

    def test_a_failing_precondition_denies_the_dispatch_with_its_condition_checks(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H2, Output construction: `fail → deny`, with
        `permissionDecisionReason` serializing every `conditionChecks[]` entry so the
        orchestrator receives exactly the report content.
        """
        open_turn()
        harness_stub.answers(
            "check-step-preconditions",
            build_report(
                "check-step-preconditions",
                "fail",
                conditionChecks=[
                    build_failing_condition_check(
                        "report-exists", "no artifact matches 'review-report'"
                    )
                ],
            ),
        )

        run = dispatch("PreToolUse", _dispatch_call())

        assert run.exit_code == 0
        rendered = run.decision()["hookSpecificOutput"]
        assert rendered["permissionDecision"] == "deny"
        assert "report-exists" in rendered["permissionDecisionReason"]
        assert (
            "no artifact matches 'review-report'"
            in rendered["permissionDecisionReason"]
        )

    def test_an_unavailable_harness_denies_rather_than_erring_open(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H2, invariant 1 (deny-by-default): any non-`pass` outcome — harness
        errors included — denies; erring open would unmake the enforcement. Still exit 0:
        a completed hook returning `deny` is what blocks, never exit 2.
        """
        open_turn()
        harness_stub.fails("check-step-preconditions", "condition evaluator crashed")

        run = dispatch("PreToolUse", _dispatch_call())

        assert run.exit_code == 0
        rendered = run.decision()["hookSpecificOutput"]
        assert rendered["permissionDecision"] == "deny"
        assert "condition evaluator crashed" in rendered["permissionDecisionReason"]

    def test_a_dispatch_with_no_in_flight_step_passes_through(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H2, Harness invocation: a dispatch with no matching in-flight step
        is already `not-applicable`, which the adapter renders as pass-through (exit 0,
        empty output) — not as a denial.
        """
        open_turn()
        harness_stub.answers(
            "check-step-preconditions",
            build_report("check-step-preconditions", "not-applicable"),
        )

        run = dispatch("PreToolUse", _dispatch_call())

        assert run.exit_code == 0
        assert run.is_silent

    def test_a_dispatch_of_an_unregistered_conversation_reaches_no_harness_function(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H2, invariant 3 (C7): non-framework dispatches pass through
        untouched and unlogged — no session, no invocation, no journal entry.
        """
        run = dispatch("PreToolUse", _dispatch_call())

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()
