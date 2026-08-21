"""H5 `PostToolUse` (write class) end to end — the commit gate on the landed write."""

from __future__ import annotations

from typing import Callable

from harness_stub import HarnessStub, build_report
from hook_dispatch import HookRun
from host_events import TURN_SESSION_ID, post_tool_use
from stub_framework import Framework


class TestWriteEndedHook:
    """Adapter spec H5 — write-ended (function 9), the commit gate."""

    def test_a_valid_write_answers_plain_success_after_one_set_wide_invocation(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H5, invariant 2: ONE function-9 invocation per tool call carrying
        the WHOLE path set (unlike H3's per-path fan-out) — and `valid` renders plain
        success, because the commit already happened harness-side.
        """
        open_turn()

        run = dispatch(
            "PostToolUse",
            post_tool_use(
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
        assert run.decision() == {"continue": True}
        commit_gate = harness_stub.find_calls("check-step-artifact")
        assert len(commit_gate) == 1
        assert commit_gate[0].values("--artifact-path") == (
            "portfolio/first.md",
            "portfolio/second.md",
        )
        assert commit_gate[0].value("--session-id") == TURN_SESSION_ID

    def test_a_reverted_write_blocks_with_the_failure_message_and_retry_directive(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H5, Output construction: `reverted` renders `decision: block` whose
        `reason` carries each `artifactChecks[]` path, `failureMessage` and revert record
        verbatim, plus the rewrite-and-retry directive as `additionalContext`.
        """
        open_turn()
        harness_stub.answers(
            "check-step-artifact",
            build_report(
                "check-step-artifact",
                "reverted",
                artifactChecks=[
                    {
                        "artifactPath": "portfolio/first.md",
                        "failureMessage": "frontmatter.status: 'shipped' is not one of the enum values",
                        "revert": {"action": "restored"},
                    }
                ],
            ),
        )

        run = dispatch(
            "PostToolUse",
            post_tool_use(
                "create_file", {"filePath": framework.workspace_path("portfolio/first.md")}
            ),
        )

        assert run.exit_code == 0
        decision = run.decision()
        assert decision["decision"] == "block"
        assert "portfolio/first.md" in decision["reason"]
        assert "restored" in decision["reason"]
        assert "Rewrite the artifact" in (
            decision["hookSpecificOutput"]["additionalContext"]
        )

    def test_an_unavailable_harness_blocks_rather_than_reporting_a_silent_success(
        self,
        open_turn: Callable[[], HookRun],
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H5, Output construction / invariant 3: harness errors block — the
        hook never returns `valid` without the commit having succeeded. Still exit 0: the
        block is the structured decision, not the exit code.
        """
        open_turn()
        harness_stub.fails("check-step-artifact", "artifact store unreachable")

        run = dispatch(
            "PostToolUse",
            post_tool_use(
                "create_file", {"filePath": framework.workspace_path("portfolio/first.md")}
            ),
        )

        assert run.exit_code == 0
        decision = run.decision()
        assert decision["decision"] == "block"
        assert "artifact store unreachable" in decision["reason"]

    def test_a_write_of_an_unregistered_conversation_passes_through(
        self,
        dispatch: Callable[..., HookRun],
        harness_stub: HarnessStub,
        framework: Framework,
    ) -> None:
        """Adapter spec H5 (as H3, Harness invocation / C7): with no registered session the
        write-ended boundary invokes nothing — exit 0, empty output, nothing journaled.
        """
        run = dispatch(
            "PostToolUse",
            post_tool_use(
                "create_file", {"filePath": framework.workspace_path("portfolio/first.md")}
            ),
        )

        assert run.exit_code == 0
        assert run.is_silent
        assert harness_stub.calls == ()
