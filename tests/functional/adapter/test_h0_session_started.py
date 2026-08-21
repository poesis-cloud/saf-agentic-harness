"""H0 `UserPromptSubmit` end to end — the orchestrator turn opens and its context injects.

Driven through a real `dispatch.sh` subprocess: the in-process orchestration is the unit
suite's subject, the host-observable answer (stdout shape + exit code) is this one's.
"""

from __future__ import annotations

from typing import Callable

from harness_stub import HarnessStub, build_report
from hook_dispatch import HookRun
from host_events import ORCHESTRATOR_AGENT, TURN_SESSION_ID, user_prompt_submit
from stub_framework import INSTRUCTION_BODY, INSTRUCTION_REF, SKILL_ID, Framework


class TestSessionStartedHook:
    """Adapter spec H0 — session-started (functions 0, 1, 2), agent-scoped."""

    def test_opens_the_turn_session_before_resolving_any_context(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H0, invariant 1: registration precedes everything at this session's
        level, physically — function 0 runs before functions 1-2 within the same hook
        handling, and the id is `<sanitized session_id>-t<sanitized timestamp>`.
        """
        run = dispatch("UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR_AGENT)

        assert run.exit_code == 0
        assert harness_stub.functions == (
            "start-session",
            "resolve-workflow-instructions",
            "resolve-workflow-skills",
        )
        registration = harness_stub.find_calls("start-session")[0]
        assert registration.value("--session-id") == TURN_SESSION_ID
        assert registration.value("--agent") == ORCHESTRATOR_AGENT

    def test_injects_the_orchestrator_context_as_the_turn_decision(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H0, Output construction: instruction refs are INLINED under a
        header naming each ref and skill ids become LOAD DIRECTIVES, in one
        `additionalContext` string on the `UserPromptSubmit` decision shape.
        """
        run = dispatch("UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR_AGENT)

        assert run.exit_code == 0
        injected = run.decision()["hookSpecificOutput"]
        assert injected["hookEventName"] == "UserPromptSubmit"
        context = injected["additionalContext"]
        assert f"## {INSTRUCTION_REF}" in context
        assert INSTRUCTION_BODY in context
        assert f"skills/{SKILL_ID}.skill.md" in context
        assert "# Code review" not in context

    def test_a_failing_harness_reports_a_system_message_and_never_vetoes_the_prompt(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H0, Output construction: on a harness error, exit 0 with a
        `systemMessage` naming the failure — never exit 2, and never `decision: block`
        (this boundary must not veto the user's message).
        """
        harness_stub.fails("resolve-workflow-instructions", "workflow catalog unreadable")

        run = dispatch("UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR_AGENT)

        assert run.exit_code == 0
        decision = run.decision()
        assert "workflow catalog unreadable" in decision["systemMessage"]
        assert "decision" not in decision
        assert "hookSpecificOutput" not in decision

    def test_a_non_framework_registration_passes_through_without_resolving_context(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H0 / C7: `not-applicable` from function 0 means this turn belongs
        to no framework session — pass-through (exit 0, empty stdout) and no further
        function is invoked.
        """
        harness_stub.answers(
            "start-session", build_report("start-session", "not-applicable")
        )

        run = dispatch("UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR_AGENT)

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.functions == ("start-session",)

    def test_a_context_ref_that_cannot_be_rendered_is_reported_not_crashed(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H0, Output construction: "on a harness error: exit 0 with a
        `systemMessage` naming the failure" — an instruction ref the harness returned but
        the adapter cannot resolve against `FRAMEWORK_INSTRUCTIONS_DIR` is exactly such a
        failure, and it must be REPORTED at this boundary, not thrown: a hook that dies
        answers no decision at all, so the failure would be invisible to the model.
        """
        harness_stub.answers(
            "resolve-workflow-instructions",
            build_report(
                "resolve-workflow-instructions", "resolved", instructions=["vanished-ref"]
            ),
        )

        run = dispatch("UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR_AGENT)

        assert run.exit_code == 0
        assert "vanished-ref" in run.decision()["systemMessage"]

    def test_the_injected_instruction_is_read_from_this_framework_only(
        self, dispatch: Callable[..., HookRun], framework: Framework
    ) -> None:
        """Adapter spec H0, Output construction rule 1: instruction refs resolve against
        `FRAMEWORK_INSTRUCTIONS_DIR` — the environment's framework, never the process's.
        """
        (framework.root / "instructions" / f"{INSTRUCTION_REF}.instructions.md").write_text(
            "REPLACED BODY\n", encoding="utf-8"
        )

        run = dispatch("UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR_AGENT)

        assert run.exit_code == 0
        assert "REPLACED BODY" in run.decision()["hookSpecificOutput"]["additionalContext"]
