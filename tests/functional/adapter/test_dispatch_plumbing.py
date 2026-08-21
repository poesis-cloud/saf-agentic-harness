"""The invocation plumbing itself: `dispatch.sh`'s contract, C7 pass-through, exit codes.

Adapter spec — "Invocation plumbing and contract layering": seam 2 (`dispatch.sh`) has no
contract of its own, only argv shaping; seam 4 is structured stdout JSON ON EXIT 0. These
tests hold the shim and the exit plane to exactly that, across every registered event.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from harness_stub import HarnessStub
from hook_dispatch import HookRun, run_hook
from host_events import (
    ORCHESTRATOR_AGENT,
    TURN_SESSION_ID,
    dispatch_tool_input,
    post_tool_use,
    pre_tool_use,
    stop,
    subagent_start,
    subagent_stop,
    user_prompt_submit,
)
from stub_framework import REPO_ADAPTERS_DIR, Framework

_EVERY_REGISTERED_FIRING: Mapping[str, Any] = {
    "UserPromptSubmit": user_prompt_submit(),
    "SubagentStart": subagent_start(),
    "PreToolUse": pre_tool_use("runSubagent", dispatch_tool_input()),
    "PostToolUse": post_tool_use("runSubagent", dispatch_tool_input()),
    "SubagentStop": subagent_stop(),
    "Stop": stop(),
}


class TestDispatchArgumentContract:
    """Adapter spec — Invocation plumbing, seam 2: `dispatch.sh <event> <env> [<agent>]`."""

    def test_the_third_argument_carries_the_scoping_agent_into_the_registration(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec H0 / I13: the scoping agent slug is a DISPATCH ARGUMENT, never a
        payload field — the rendered `.agent.md` block passes the orchestrator's own slug
        as `dispatch.sh`'s third argument, and it must reach function 0 as `agent`.
        """
        run = dispatch("UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR_AGENT)

        assert run.exit_code == 0
        assert harness_stub.find_calls("start-session")[0].value("--agent") == (
            ORCHESTRATOR_AGENT
        )

    def test_the_two_argument_form_names_no_agent_at_all(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub
    ) -> None:
        """Adapter spec — Rendered registration: the workspace hooks file passes only
        `<event> <env>`, so nothing must invent an agent for those firings; the flag is
        absent rather than empty or defaulted.
        """
        run = dispatch("UserPromptSubmit", user_prompt_submit())

        assert run.exit_code == 0
        registration = harness_stub.find_calls("start-session")[0]
        assert not registration.carries("--agent")
        assert registration.value("--session-id") == TURN_SESSION_ID

    def test_the_copied_dispatch_entry_is_the_repository_script_verbatim(
        self, framework: Framework
    ) -> None:
        """Adapter spec — Invocation plumbing, seam 2: this suite runs a tmp COPY of the
        repository's `adapters/` tree for hermeticity, so the copy must be the shipped
        script byte for byte — otherwise every result here would be about a different
        entry point than the host actually execs.
        """
        shipped = (REPO_ADAPTERS_DIR / "dispatch.sh").read_bytes()

        assert framework.dispatch_path.read_bytes() == shipped

    def test_the_shipped_script_answers_a_foreign_firing_without_touching_anything(
        self, framework: Framework, harness_stub: HarnessStub
    ) -> None:
        """Adapter spec C7 / I5: fired at the SHIPPED `adapters/dispatch.sh` itself, a
        non-framework firing still answers exit 0 with empty stdout and leaves no private
        adapter state behind — the pass-through is genuinely inert.
        """
        shipped_dispatch = REPO_ADAPTERS_DIR / "dispatch.sh"
        tracker_record = (
            REPO_ADAPTERS_DIR / "vscode-github-copilot-chat" / ".session-tracker.json"
        )
        recorded_before = tracker_record.exists()

        run = run_hook(
            dispatch_path=shipped_dispatch,
            event="PreToolUse",
            payload=pre_tool_use("read_file", {"filePath": "README.md"}),
            environment=framework.environment,
            cwd=framework.root,
        )

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()
        assert tracker_record.exists() is recorded_before


class TestPassThrough:
    """Adapter spec C7 — every firing matching no declared class is a pass-through."""

    @pytest.mark.parametrize(
        ("event", "tool_name"),
        (
            ("PreToolUse", "read_file"),
            ("PostToolUse", "read_file"),
            ("PreToolUse", "fetch_webpage"),
        ),
    )
    def test_a_firing_of_an_undeclared_tool_produces_no_decision_and_no_journal(
        self,
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        open_turn: Callable[[], HookRun],
        event: str,
        tool_name: str,
    ) -> None:
        """Adapter spec — Boundary binding / C7: the host has NO per-tool matcher, so one
        registration fires for EVERY tool call; a tool this adapter's `tools.yaml` does not
        declare is a pass-through — exit 0, empty stdout, no journal entry — even inside a
        fully registered framework turn.
        """
        open_turn()
        harness_stub.forget_calls()

        payload = (
            pre_tool_use(tool_name, {"filePath": "README.md"})
            if event == "PreToolUse"
            else post_tool_use(tool_name, {"filePath": "README.md"})
        )
        run = dispatch(event, payload)

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()


class TestExitPlane:
    """Adapter spec I5 — the canonical control surface is structured stdout on exit 0."""

    @pytest.mark.parametrize("event", tuple(_EVERY_REGISTERED_FIRING))
    def test_a_wholly_unavailable_harness_never_makes_a_hook_exit_non_zero(
        self,
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        open_turn: Callable[[], HookRun],
        event: str,
    ) -> None:
        """Adapter spec I5 / Host hook engine — Exit-code semantics: exit 2 is only the
        hard-failure fallback and this adapter never takes it; every deliberate answer —
        success, deny, block, pass-through and harness error alike — is exit 0 with a
        structured (or empty) stdout. Asserted with EVERY harness command failing.
        """
        open_turn()
        harness_stub.fails_every_function("harness core unavailable")

        run = dispatch(event, _EVERY_REGISTERED_FIRING[event], ORCHESTRATOR_AGENT)

        assert run.exit_code == 0
        assert run.is_silent or run.decision() is not None

    def test_a_denying_firing_still_exits_zero_with_its_decision_on_stdout(
        self, dispatch: Callable[..., HookRun], harness_stub: HarnessStub,
        open_turn: Callable[[], HookRun],
    ) -> None:
        """Adapter spec I5: a deny is a STRUCTURED STDOUT decision on exit 0 — the host
        honors it as such; exit 2 (stderr to the model) is never how this adapter blocks.
        """
        open_turn()
        harness_stub.fails("check-step-preconditions", "condition evaluator crashed")

        run = dispatch("PreToolUse", pre_tool_use("runSubagent", dispatch_tool_input()))

        assert run.exit_code == 0
        assert run.decision()["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_the_suite_never_reads_the_developers_own_framework(
        self, framework: Framework
    ) -> None:
        """Local convention (spec — Unit testing: isolation by construction, never by
        monkey-patching internals): the hook process's environment is built from scratch,
        so no exported `FRAMEWORK_*` of the developer's can reach it and every path a hook
        resolves lies inside the tmp framework.
        """
        resolved = {
            key: value
            for key, value in framework.environment.items()
            if key.startswith("FRAMEWORK_") or key.startswith("HARNESS_")
        }

        assert set(resolved) == {
            "FRAMEWORK_DIR",
            "FRAMEWORK_WORKSPACE_DIR",
            "FRAMEWORK_INSTRUCTIONS_DIR",
            "FRAMEWORK_SKILLS_DIR",
            "HARNESS_STUB_SCRIPT",
            "HARNESS_STUB_JOURNAL",
        }
        assert Path(resolved["FRAMEWORK_DIR"]) == framework.root
