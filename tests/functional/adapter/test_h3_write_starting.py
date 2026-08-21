"""H3 `PreToolUse` (write class) end to end — every artifact path is authorized first."""

from __future__ import annotations

from typing import Any, Callable

from harness_stub import HarnessStub, build_error_report, build_report
from hook_dispatch import HookRun
from host_events import (
    STEP_AGENT_ID,
    TURN_SESSION_ID,
    pre_tool_use,
    subagent_start,
)
from stub_framework import Framework


def _authorization(action: str = "update", **overrides: Any) -> dict[str, Any]:
    return {
        "actor": "qa-engineer",
        "artifactPath": "portfolio/epics/epic-payments.md",
        "action": action,
        "resource": "epic",
        **overrides,
    }


class TestWriteStartingHook:
    """Adapter spec H3 — write-starting (function 8), live write authorization."""

    def test_authorizes_every_declared_path_of_the_call_relative_to_the_workspace(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H3 / I8: function 8 runs once PER artifact path — including the
        nested `replacements[].filePath` fan-out no flat key reaches — with absolute host
        paths relativized to the workspace root and `action` mapped from `tool_name`.
        """
        open_turn()

        run = dispatch(
            "PreToolUse",
            pre_tool_use(
                "multi_replace_string_in_file",
                {
                    "replacements": [
                        {"filePath": framework.workspace_path("portfolio/first.md")},
                        {"filePath": framework.workspace_path("portfolio/second.md")},
                    ]
                },
            ),
        )

        assert run.exit_code == 0
        assert run.decision()["hookSpecificOutput"]["permissionDecision"] == "allow"
        authorizations = harness_stub.find_calls("check-step-authorization")
        assert [call.value("--artifact-path") for call in authorizations] == [
            "portfolio/first.md",
            "portfolio/second.md",
        ]
        assert {call.value("--action") for call in authorizations} == {"update"}

    def test_one_denied_path_denies_the_whole_multi_path_call(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H3, invariant 2: multi-path calls are all-or-nothing at the host
        surface — ONE `permissionDecision` guards the whole tool call, so an allowed path
        beside a denied one still denies, carrying the denial's `failureMessage`.
        """
        open_turn()
        harness_stub.answers_in_turn(
            "check-step-authorization",
            (
                build_report(
                    "check-step-authorization", "allowed", authorization=_authorization()
                ),
                build_report(
                    "check-step-authorization",
                    "denied",
                    authorization=_authorization(
                        failureMessage="missing privilege: update epic"
                    ),
                ),
            ),
        )

        run = dispatch(
            "PreToolUse",
            pre_tool_use(
                "multi_replace_string_in_file",
                {
                    "replacements": [
                        {"filePath": framework.workspace_path("portfolio/first.md")},
                        {"filePath": framework.workspace_path("portfolio/second.md")},
                    ]
                },
            ),
        )

        assert run.exit_code == 0
        rendered = run.decision()["hookSpecificOutput"]
        assert rendered["permissionDecision"] == "deny"
        assert "missing privilege: update epic" in rendered["permissionDecisionReason"]

    def test_a_harness_error_denies_the_write_rather_than_erring_open(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H3, invariant 3 (deny-by-default, as H2 invariant 1): a harness
        error outcome maps to `deny` with the error detail as reason — never a silent
        allow, and never exit 2.
        """
        open_turn()
        harness_stub.answers(
            "check-step-authorization",
            build_error_report(
                "check-step-authorization",
                code="artifact-schema-unresolved",
                message="No artifact schema resolves this path.",
            ),
        )

        run = dispatch(
            "PreToolUse",
            pre_tool_use(
                "create_file", {"filePath": framework.workspace_path("portfolio/new.md")}
            ),
        )

        assert run.exit_code == 0
        rendered = run.decision()["hookSpecificOutput"]
        assert rendered["permissionDecision"] == "deny"
        assert "artifact-schema-unresolved" in rendered["permissionDecisionReason"]

    def test_a_step_write_is_attributed_to_the_step_session_under_its_dispatcher(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H3, Harness invocation: `session_id` resolves to the session the
        tool call RUNS IN — the step session once H1 pushed it — with the dispatching turn
        session as `parentSessionId`.
        """
        open_turn()
        dispatch("SubagentStart", subagent_start())

        run = dispatch(
            "PreToolUse",
            pre_tool_use(
                "create_file", {"filePath": framework.workspace_path("portfolio/new.md")}
            ),
        )

        assert run.exit_code == 0
        authorization = harness_stub.find_calls("check-step-authorization")[0]
        assert authorization.value("--session-id") == STEP_AGENT_ID
        assert authorization.value("--parent-session-id") == TURN_SESSION_ID
        assert authorization.value("--action") == "create"

    def test_a_write_naming_no_path_reaches_no_harness_function(
        self, open_turn: Callable[[], HookRun], dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
    ) -> None:
        """Adapter spec H3, Harness invocation: path extraction probes `pathKeys` and the
        nested expressions only — a call reaching no artifact path invokes nothing, so the
        harness never sees a write it has no path for.
        """
        open_turn()

        run = dispatch("PreToolUse", pre_tool_use("create_file", {"content": "text"}))

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.find_calls("check-step-authorization") == ()

    def test_a_write_of_an_unregistered_conversation_passes_through(
        self,
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H3, Harness invocation / C7: an unregistered session's write passes
        through — exit 0, empty output, unlogged; the harness governs framework sessions
        only.
        """
        run = dispatch(
            "PreToolUse",
            pre_tool_use(
                "create_file", {"filePath": framework.workspace_path("portfolio/new.md")}
            ),
        )

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()
