"""Unit tests for `HookClassifier` — host event + tool class into exactly one `EventClass`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from event_class import EventClass
from hook_binding import load_hook_binding
from hook_classifier import HookClassifier
from hook_event import HookEvent


@pytest.fixture
def classifier(adapter_dir: Path) -> HookClassifier:
    return HookClassifier(load_hook_binding(adapter_dir))


def _event(payload: Mapping[str, Any]) -> HookEvent:
    return HookEvent.build_from_payload(payload)


class TestHookClassifier:
    """Adapter spec I14: every firing classifies into exactly one closed case."""

    def test_classifies_user_prompt_submit_as_session_started(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H0: the agent-scoped `UserPromptSubmit` IS the session-started
        boundary — one orchestrator agent session per chat request.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "2026-07-11T14:32:07.000Z",
                "hook_event_name": "UserPromptSubmit",
                "session_id": "chat-session-guid",
                "prompt": "…the user's message…",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.SESSION_STARTED

    def test_classifies_subagent_start_as_step_started(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H1: `SubagentStart` is the step-started boundary — it can only
        inject, never deny.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "2026-07-11T14:32:07.000Z",
                "hook_event_name": "SubagentStart",
                "session_id": "chat-session-guid",
                "agent_id": "subagent-invocation-id",
                "agent_type": "qa-engineer",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.STEP_STARTED

    def test_classifies_a_dispatch_pre_tool_use_as_step_starting(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H2: `PreToolUse` with `tool_name` in `dispatchTools`
        (`runSubagent`) is THE step-precondition enforcement point.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PreToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "runSubagent",
                "tool_input": {"agentName": "qa-engineer"},
                "tool_use_id": "call_abc123",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.STEP_STARTING

    def test_classifies_a_write_pre_tool_use_as_write_starting(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H3: `PreToolUse` with `tool_name` in `writeTools` is the
        write-starting boundary.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PreToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "create_file",
                "tool_input": {"filePath": "portfolio/epics/epic-payments.md"},
                "tool_use_id": "call_def456",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.WRITE_STARTING

    def test_classifies_a_delete_pre_tool_use_as_write_starting(
        self, adapter_dir: Path
    ) -> None:
        """Adapter spec H3: classification is by `tool_name ∈ writeTools ∪ deleteTools`."""
        binding = replace(load_hook_binding(adapter_dir), delete_tools=("delete_file",))
        deleting = HookClassifier(binding)

        event = _event(
            {
                "timestamp": "…",
                "hook_event_name": "PreToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "delete_file",
                "tool_input": {"filePath": "portfolio/epics/epic-payments.md"},
                "tool_use_id": "call_del",
            }
        )

        assert deleting.classify_event(event) is EventClass.WRITE_STARTING

    def test_classifies_a_mediated_command_pre_tool_use_as_mediated_attribution(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H4: `PreToolUse` on a `mediatedCommandTools` tool is the mediated
        attribution surface.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PreToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "run_in_terminal",
                "tool_input": {"command": "harness.py resolve-step --workflow verification"},
                "tool_use_id": "call_ghi789",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.MEDIATED_ATTRIBUTION

    def test_classifies_a_guarded_shell_pre_tool_use_as_mediated_attribution(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H4 rule 1 / I9: a `guardedShellTools` tool that is not a mediated
        command tool still reaches H4 — the guarded-shell check is its fall-through.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PreToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "create_and_run_task",
                "tool_input": {"command": "make build"},
                "tool_use_id": "call_task",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.MEDIATED_ATTRIBUTION

    def test_classifies_an_undeclared_pre_tool_use_as_pass_through(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec — Boundary binding (C7): a `PreToolUse` whose `tool_name` matches
        no declared class is passed through, unlogged.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PreToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "read_file",
                "tool_input": {"filePath": "portfolio/epics/epic-payments.md"},
                "tool_use_id": "call_read",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.PASS_THROUGH

    def test_classifies_a_write_post_tool_use_as_write_ended(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H5: `PostToolUse` on a write tool is the commit gate."""
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PostToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "create_file",
                "tool_input": {"filePath": "portfolio/payments/features/feature-refunds.md"},
                "tool_response": "Created file …",
                "tool_use_id": "call_def456",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.WRITE_ENDED

    def test_classifies_a_dispatch_post_tool_use_as_step_ended(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H6: `PostToolUse` on a dispatch tool is THE evaluation point."""
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PostToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "runSubagent",
                "tool_input": {"agentName": "qa-engineer"},
                "tool_response": "…the subagent's final report…",
                "tool_use_id": "call_abc123",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.STEP_ENDED

    def test_classifies_an_undeclared_post_tool_use_as_pass_through(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec — Boundary binding (C7): observational events are adapter
        telemetry, not harness functions.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PostToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "read_file",
                "tool_input": {"filePath": "README.md"},
                "tool_response": "…",
                "tool_use_id": "call_read",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.PASS_THROUGH

    def test_classifies_a_mediated_command_post_tool_use_as_pass_through(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H4: mediated attribution is a PRE-tool boundary only — the same
        tool on `PostToolUse` reaches no harness function.
        """
        payload = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "PostToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "run_in_terminal",
                "tool_input": {"command": "harness.py resolve-step --workflow verification"},
                "tool_response": "…",
                "tool_use_id": "call_ghi789",
            }
        )

        assert classifier.classify_event(_event(payload)) is EventClass.PASS_THROUGH

    def test_classifies_subagent_stop_and_stop_as_session_ended(
        self, classifier: HookClassifier, assert_valid_stdin
    ) -> None:
        """Adapter spec H7: `SubagentStop` and `Stop` are the session-ended boundary."""
        subagent_stop = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "SubagentStop",
                "session_id": "chat-session-guid",
                "agent_id": "subagent-invocation-id",
            }
        )
        stop = assert_valid_stdin(
            {
                "timestamp": "…",
                "hook_event_name": "Stop",
                "session_id": "chat-session-guid",
            }
        )

        assert classifier.classify_event(_event(subagent_stop)) is EventClass.SESSION_ENDED
        assert classifier.classify_event(_event(stop)) is EventClass.SESSION_ENDED

    def test_classifies_an_unregistered_host_event_as_pass_through(
        self, classifier: HookClassifier
    ) -> None:
        """Adapter spec — Boundary binding: `SessionStart`, `PreCompact`, `SessionEnd`,
        and `ErrorOccurred` are NOT registered — a firing of one reaches no boundary.
        """
        for event_name in ("SessionStart", "PreCompact", "SessionEnd", "ErrorOccurred"):
            event = _event(
                {
                    "timestamp": "…",
                    "hook_event_name": event_name,
                    "session_id": "chat-session-guid",
                }
            )

            assert classifier.classify_event(event) is EventClass.PASS_THROUGH
