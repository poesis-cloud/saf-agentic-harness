"""Unit tests for `Adapter` — event orchestration over the harness command API only."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import pytest

from adapter import Adapter, run_hook_entry
from conftest import FakeCommandRunner, build_error_report, build_report, queue_reports
from hook_binding import load_hook_binding
from hook_classifier import HookClassifier
from hook_event import HookDecision, HookEvent
from hook_renderer import HookRenderer
from session_tracker import SessionTracker

HOST_SESSION = "chat-session-guid"
TURN_SESSION = "chat-session-guid-t2026-07-11t14-32-07-000z"
TIMESTAMP = "2026-07-11T14:32:07.000Z"


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    (workspace / "portfolio").mkdir(parents=True)
    return workspace


@pytest.fixture
def tracker(tmp_path: Path) -> SessionTracker:
    return SessionTracker(tmp_path / "sessions.json")


@pytest.fixture
def renderer(tmp_path: Path) -> HookRenderer:
    instructions_dir = tmp_path / "instructions"
    skills_dir = tmp_path / "skills"
    instructions_dir.mkdir()
    skills_dir.mkdir()
    (instructions_dir / "reports-handling.instructions.md").write_text(
        "Never surface step details to the user.\n", encoding="utf-8"
    )
    return HookRenderer(instructions_dir=instructions_dir, skills_dir=skills_dir)


@pytest.fixture
def make_adapter(adapter_dir: Path, tracker: SessionTracker, renderer: HookRenderer, workspace_dir: Path):
    def _make(
        runner: FakeCommandRunner,
        guarded_path_markers: tuple[str, ...] = ("portfolio/", "logs/"),
    ) -> Adapter:
        binding = replace(
            load_hook_binding(adapter_dir), guarded_path_markers=guarded_path_markers
        )
        return Adapter(
            binding=binding,
            classifier=HookClassifier(binding),
            tracker=tracker,
            renderer=renderer,
            command_runner=runner,
            workspace_dir=workspace_dir,
        )

    return _make


def _event(payload: Mapping[str, Any], agent: str | None = None) -> HookEvent:
    return HookEvent.build_from_payload(payload, scoping_agent=agent)


def _prompt_payload() -> dict[str, Any]:
    return {
        "timestamp": TIMESTAMP,
        "hook_event_name": "UserPromptSubmit",
        "session_id": HOST_SESSION,
        "prompt": "…the user's message…",
    }


def _subagent_start_payload(agent_type: str = "qa-engineer") -> dict[str, Any]:
    return {
        "timestamp": TIMESTAMP,
        "hook_event_name": "SubagentStart",
        "session_id": HOST_SESSION,
        "agent_id": "subagent-invocation-id",
        "agent_type": agent_type,
    }


def _pre_tool_payload(tool_name: str, tool_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": TIMESTAMP,
        "hook_event_name": "PreToolUse",
        "session_id": HOST_SESSION,
        "tool_name": tool_name,
        "tool_input": dict(tool_input),
        "tool_use_id": "call_abc123",
    }


def _post_tool_payload(tool_name: str, tool_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": TIMESTAMP,
        "hook_event_name": "PostToolUse",
        "session_id": HOST_SESSION,
        "tool_name": tool_name,
        "tool_input": dict(tool_input),
        "tool_response": "…",
        "tool_use_id": "call_abc123",
    }


def _rendered(decision: HookDecision) -> dict[str, Any]:
    return json.loads(decision.stdout)


class TestAdapter:
    """Adapter spec I14/I15 — sequencing, fan-out, and gating over the command API."""

    def test_sequences_registration_before_the_two_workflow_resolutions(
        self, make_adapter, assert_valid_stdin, assert_valid_inquiry
    ) -> None:
        """Adapter spec H0, invariant 1: registration precedes everything at this
        session's level — function 0 runs before functions 1–2 within the same handling.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        payload = assert_valid_stdin(_prompt_payload())

        adapter.handle_hook_event(_event(payload, agent="value-management-officer"))

        assert runner.list_functions() == [
            "start-session",
            "resolve-workflow-instructions",
            "resolve-workflow-skills",
        ]
        assert_valid_inquiry("start-session", runner.calls[0].inquiry)

    def test_registers_the_turn_session_derived_from_host_data_with_a_null_parent(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec — Session identity binding: an orchestrator turn's `sessionId` is
        `<sanitized session_id>-t<sanitized timestamp>` with `parentSessionId: null` (a
        user prompt opens it — a root), and the id is host-observed, never model-authored.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)

        adapter.handle_hook_event(
            _event(_prompt_payload(), agent="value-management-officer")
        )

        assert runner.calls[0].inquiry == {
            "agent": "value-management-officer",
            "sessionId": TURN_SESSION,
            "parentSessionId": None,
        }
        assert tracker.resolve_current(HOST_SESSION) == TURN_SESSION

    def test_injects_the_workflow_context_of_the_scoping_agent(
        self, make_adapter, assert_valid_stdout
    ) -> None:
        """Adapter spec H0, Postconditions: the request's context contains exactly the
        orchestrator's inlined instructions and skill load directives — nothing chosen by
        the agent.
        """
        runner = FakeCommandRunner(
            reports=queue_reports(
                resolve_workflow_instructions=[
                    build_report(
                        "resolve-workflow-instructions",
                        "resolved",
                        instructions=["reports-handling"],
                    )
                ]
            )
        )
        adapter = make_adapter(runner)

        decision = adapter.handle_hook_event(
            _event(_prompt_payload(), agent="value-management-officer")
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "Never surface step details to the user." in (
            rendered["hookSpecificOutput"]["additionalContext"]
        )

    def test_passes_through_a_session_start_the_core_reports_not_applicable(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H1, Preconditions / C7: `not-applicable` from function 0 renders
        as pass-through — exit 0, empty output — and nothing is left current to
        misattribute later firings to.
        """
        runner = FakeCommandRunner(
            reports=queue_reports(
                start_session=[build_report("start-session", "not-applicable")]
            )
        )
        adapter = make_adapter(runner)

        decision = adapter.handle_hook_event(
            _event(_prompt_payload(), agent="value-management-officer")
        )

        assert (decision.exit_code, decision.stdout) == (0, "")
        assert runner.list_functions() == ["start-session"]
        assert tracker.resolve_current(HOST_SESSION) is None

    def test_never_vetoes_the_user_message_when_the_harness_errors(
        self, make_adapter, assert_valid_stdout
    ) -> None:
        """Adapter spec H0, Output construction: on a harness error, exit 0 with a
        `systemMessage` — never exit 2; an uninstructed orchestrator is observable in the
        journal and cannot pass any later boundary.
        """
        runner = FakeCommandRunner(failure=RuntimeError("harness command crashed"))
        adapter = make_adapter(runner)

        decision = adapter.handle_hook_event(
            _event(_prompt_payload(), agent="value-management-officer")
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert decision.exit_code == 0
        assert "harness command crashed" in rendered["systemMessage"]

    def test_registers_a_step_session_under_the_dispatching_session_then_pushes_it(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdin, assert_valid_inquiry
    ) -> None:
        """Adapter spec H1: function 0 carries `agent_type` as the actor, the sanitized
        `agent_id` as the session, and the dispatching orchestrator's CURRENT agent
        session as parent; after resolving the parent the adapter PUSHES the step session
        as current, then resolves functions 6 and 7.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)
        payload = assert_valid_stdin(_subagent_start_payload())

        adapter.handle_hook_event(_event(payload))

        assert runner.list_functions() == [
            "start-session",
            "resolve-step-instructions",
            "resolve-step-skills",
        ]
        assert runner.calls[0].inquiry == {
            "agent": "qa-engineer",
            "sessionId": "subagent-invocation-id",
            "parentSessionId": TURN_SESSION,
        }
        assert_valid_inquiry("resolve-step-instructions", runner.calls[1].inquiry)
        assert tracker.resolve_current(HOST_SESSION) == "subagent-invocation-id"

    def test_passes_through_a_subagent_start_of_an_unregistered_conversation(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec — correlation scenario 8 (C7): a firing for a `session_id` never
        seen before is a pass-through — no harness function is invoked at all.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)

        decision = adapter.handle_hook_event(_event(_subagent_start_payload()))

        assert (decision.exit_code, decision.stdout) == (0, "")
        assert runner.calls == []
        assert tracker.resolve_current(HOST_SESSION) is None

    def test_pushes_nothing_for_a_foreign_subagent(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H1, invariant 3 / Session identity binding: foreign subagents
        (non-framework `agent_type`) pass through untouched and unlogged — H1 registers
        and pushes nothing for those, so their tool calls remain the orchestrator's.
        """
        runner = FakeCommandRunner(
            reports=queue_reports(
                start_session=[build_report("start-session", "not-applicable")]
            )
        )
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_subagent_start_payload(agent_type="some-foreign-agent"))
        )

        assert (decision.exit_code, decision.stdout) == (0, "")
        assert tracker.resolve_current(HOST_SESSION) == TURN_SESSION

    def test_checks_step_preconditions_in_the_dispatching_session(
        self, make_adapter, tracker: SessionTracker, assert_valid_inquiry, assert_valid_stdout
    ) -> None:
        """Adapter spec H2: function 5 is always invoked when `tool_name` matches the
        binding's dispatch-tool list, in the orchestrator's turn session resolved from
        the adapter's own `SessionTracker`.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("runSubagent", {"agentName": "qa-engineer"}))
        )

        assert runner.list_functions() == ["check-step-preconditions"]
        assert runner.calls[0].inquiry == {"sessionId": TURN_SESSION, "parentSessionId": None}
        assert_valid_inquiry("check-step-preconditions", runner.calls[0].inquiry)
        assert assert_valid_stdout(decision.stdout)["hookSpecificOutput"][
            "permissionDecision"
        ] == "allow"

    def test_denies_the_dispatch_when_a_precondition_fails(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H2, Postconditions: on deny the dispatch never executes — the step
        session never opens.
        """
        runner = FakeCommandRunner(
            reports=queue_reports(
                check_step_preconditions=[
                    build_report(
                        "check-step-preconditions",
                        "fail",
                        conditionChecks=[
                            {
                                "condition": {"kind": "precondition", "slug": "report_exists"},
                                "outcome": "fail",
                                "failureMessage": "no artifact matches 'review-report'",
                            }
                        ],
                    )
                ]
            )
        )
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("runSubagent", {"agentName": "qa-engineer"}))
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "report_exists" in rendered["hookSpecificOutput"]["permissionDecisionReason"]

    def test_passes_through_a_dispatch_with_no_in_flight_step(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H2: a dispatch with no matching in-flight step is already
        `not-applicable`, which the adapter renders as pass-through (exit 0, empty).
        """
        runner = FakeCommandRunner(
            reports=queue_reports(
                check_step_preconditions=[
                    build_report("check-step-preconditions", "not-applicable")
                ]
            )
        )
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("runSubagent", {"agentName": "qa-engineer"}))
        )

        assert (decision.exit_code, decision.stdout) == (0, "")

    def test_fans_out_one_authorization_per_artifact_path_including_nested_ones(
        self, make_adapter, tracker: SessionTracker, assert_valid_inquiry
    ) -> None:
        """Adapter spec H3 / I8: function 8 is invoked once PER artifact path of the call,
        `multi_replace_string_in_file`'s `replacements[].filePath` included — 1 invocation
        = 1 journal entry.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "multi_replace_string_in_file",
                    {
                        "replacements": [
                            {"filePath": "portfolio/a.md"},
                            {"filePath": "portfolio/b.md"},
                        ]
                    },
                )
            )
        )

        calls = runner.find_calls("check-step-authorization")
        assert [call.inquiry["artifactPath"] for call in calls] == [
            "portfolio/a.md",
            "portfolio/b.md",
        ]
        assert {call.inquiry["action"] for call in calls} == {"update"}
        assert_valid_inquiry("check-step-authorization", calls[0].inquiry)

    def test_collapses_the_authorization_fan_out_into_one_host_decision(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H3, invariant 2: multi-path calls are all-or-nothing at the host
        surface — a single `permissionDecision` guards the whole tool call, and ANY denied
        path denies it.
        """
        allowed = build_report(
            "check-step-authorization",
            "allowed",
            authorization={
                "actor": "product-manager",
                "artifactPath": "portfolio/a.md",
                "action": "update",
                "resource": "epic",
            },
        )
        denied = build_report(
            "check-step-authorization",
            "denied",
            authorization={
                "actor": "product-manager",
                "artifactPath": "portfolio/b.md",
                "action": "update",
                "resource": "epic",
                "failureMessage": "missing privilege: update epic",
            },
        )
        runner = FakeCommandRunner(
            reports=queue_reports(check_step_authorization=[allowed, denied])
        )
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "multi_replace_string_in_file",
                    {
                        "replacements": [
                            {"filePath": "portfolio/a.md"},
                            {"filePath": "portfolio/b.md"},
                        ]
                    },
                )
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "portfolio/b.md" in rendered["hookSpecificOutput"]["permissionDecisionReason"]

    def test_relativizes_an_absolute_host_path_to_the_workspace_root(
        self, make_adapter, tracker: SessionTracker, workspace_dir: Path
    ) -> None:
        """Adapter spec H3: absolute host paths are relativized to the workspace root
        before invocation — the harness contract takes workspace-relative artifact paths.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "create_file",
                    {"filePath": str(workspace_dir / "portfolio/epics/epic-payments.md")},
                )
            )
        )

        assert runner.calls[0].inquiry["artifactPath"] == "portfolio/epics/epic-payments.md"
        assert runner.calls[0].inquiry["action"] == "create"

    def test_carries_the_parent_session_when_the_writer_is_a_step_session(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H3: the write hook resolves to the session the tool call runs in —
        the step (subagent) session for step writes — and carries its parent.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)
        tracker.push_current(HOST_SESSION, "subagent-invocation-id")

        adapter.handle_hook_event(
            _event(_pre_tool_payload("create_file", {"filePath": "portfolio/a.md"}))
        )

        assert runner.calls[0].inquiry["sessionId"] == "subagent-invocation-id"
        assert runner.calls[0].inquiry["parentSessionId"] == TURN_SESSION

    def test_passes_through_a_write_naming_no_path(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H3: a call reaching no artifact path invokes nothing — the harness
        governs the workspace data plane only.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("apply_patch", {"input": "no path here"}))
        )

        assert (decision.exit_code, decision.stdout) == (0, "")
        assert runner.calls == []

    def test_denies_a_write_when_the_harness_errors(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H3, invariant 3 (deny-by-default): harness errors deny, never err
        open — the workspace never sees unauthorized bytes.
        """
        runner = FakeCommandRunner(failure=RuntimeError("harness command crashed"))
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("create_file", {"filePath": "portfolio/a.md"}))
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert decision.exit_code == 0
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_a_harness_command_that_is_not_an_agent_invoked_function(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, rule 1: deny a harness invocation whose function is not
        `resolve-step` or `resolve-step-model` — every other function belongs to a hook
        boundary, and a model-authored call to one is never legitimate.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("run_in_terminal", {"command": "harness.py start-session"}))
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "start-session" in rendered["hookSpecificOutput"]["permissionDecisionReason"]
        assert runner.calls == []

    def test_denies_a_guarded_shell_command_naming_a_workspace_artifact_path(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, rule 1 fall-through / I9: a non-harness command on a
        `guardedShellTools` tool that textually references a workspace artifact-layout
        path is denied with the offending path named.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "run_in_terminal", {"command": "echo x > portfolio/epics/epic-payments.md"}
                )
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "portfolio/epics/epic-payments.md" in (
            rendered["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_denies_a_guarded_shell_command_naming_the_workspace_logs_path(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H3 / H4 rule 1: a path under the workspace logs directory is
        DENIED, never passed through — logs are harness-authored, single-writer.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload("create_and_run_task", {"command": "rm logs/session.log.jsonl"})
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "logs/session.log.jsonl" in (
            rendered["hookSpecificOutput"]["permissionDecisionReason"]
        )

    def test_passes_through_an_ordinary_shell_command(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H4, rule 1: anything that is neither a harness invocation nor a
        guarded reference passes through — advisory guard, not a blanket shell block.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("run_in_terminal", {"command": "make build"}))
        )

        assert (decision.exit_code, decision.stdout) == (0, "")
        assert runner.calls == []

    def test_denies_a_command_already_carrying_a_model_authored_session_id(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, rule 2 / invariant 1: deny any invocation whose command
        already carries session-attribution arguments — model-authored attribution is
        never accepted, never merely overwritten.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "run_in_terminal",
                    {"command": "harness.py resolve-step --workflow v --session-id forged"},
                )
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "--session-id" in rendered["hookSpecificOutput"]["permissionDecisionReason"]

    def test_denies_a_command_already_carrying_a_model_authored_parent_session_id(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, rule 2: `--parent-session-id` is refused on the same ground as
        `--session-id`.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "run_in_terminal",
                    {
                        "command": (
                            "harness.py resolve-step-model --workflow v "
                            "--parent-session-id forged"
                        )
                    },
                )
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_denies_the_function_before_it_looks_at_attribution_arguments(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, Mechanics order: classification (rule 1) precedes the
        model-authored-attribution check (rule 2) — the reason names the illegitimate
        function, not the argument.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "run_in_terminal",
                    {"command": "harness.py end-session --session-id forged"},
                )
            )
        )

        reason = assert_valid_stdout(decision.stdout)["hookSpecificOutput"][
            "permissionDecisionReason"
        ]
        assert "end-session" in reason
        assert "--session-id" not in reason

    def test_denies_a_mediated_command_of_a_session_it_never_registered(
        self, make_adapter, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, rule 3 / invariant 4: deny when resolution returns None — the
        tool call IS the harness command about to execute, so letting it run un-rewritten
        would invoke a harness function with no attributable session at all.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "run_in_terminal",
                    {"command": "harness.py resolve-step --workflow verification"},
                )
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert runner.calls == []

    def test_stamps_the_resolved_session_onto_the_allowed_command(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, rule 4 / invariant 3: the executed invocation's attribution is
        fully adapter-controlled, and the hook itself invokes no harness function — it is
        attribution plumbing, not a boundary function.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "run_in_terminal",
                    {"command": "harness.py resolve-step --workflow verification"},
                )
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["updatedInput"]["command"] == (
            f"harness.py resolve-step --workflow verification --session-id {TURN_SESSION}"
        )
        assert runner.calls == []

    def test_checks_the_landed_write_once_per_tool_call_with_the_whole_path_set(
        self, make_adapter, tracker: SessionTracker, assert_valid_inquiry, assert_valid_stdout
    ) -> None:
        """Adapter spec H5, invariant 2: ONE function-9 invocation per tool call, carrying
        the whole path set — unlike H3's per-path fan-out, the commit gate is per-call
        (the set is the transaction).
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _post_tool_payload(
                    "multi_replace_string_in_file",
                    {
                        "replacements": [
                            {"filePath": "portfolio/a.md"},
                            {"filePath": "portfolio/b.md"},
                        ]
                    },
                )
            )
        )

        assert runner.list_functions() == ["check-step-artifact"]
        assert runner.calls[0].inquiry["artifactPaths"] == ["portfolio/a.md", "portfolio/b.md"]
        assert_valid_inquiry("check-step-artifact", runner.calls[0].inquiry)
        assert assert_valid_stdout(decision.stdout) == {"continue": True}

    def test_blocks_and_reports_a_reverted_write(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H5, invariant 1: the revert is the HARNESS's git action —
        `decision: block` only carries the message back to the writing agent.
        """
        runner = FakeCommandRunner(
            reports=queue_reports(
                check_step_artifact=[
                    build_report(
                        "check-step-artifact",
                        "reverted",
                        artifactChecks=[
                            {
                                "artifactPath": "portfolio/a.md",
                                "failureMessage": "frontmatter.status: 'shipped' is not one of the enum values",
                                "revert": {"action": "restored"},
                            }
                        ],
                    )
                ]
            )
        )
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_post_tool_payload("create_file", {"filePath": "portfolio/a.md"}))
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["decision"] == "block"
        assert "portfolio/a.md" in rendered["reason"]

    def test_evaluates_the_returned_step_against_the_stack_base(
        self, make_adapter, tracker: SessionTracker, assert_valid_inquiry
    ) -> None:
        """Adapter spec H6: function 10 runs in the DISPATCHING (orchestrator) session —
        the hook resolves the stack BASE, not the raw top, correct under either
        `SubagentStop`/`PostToolUse` ordering.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)
        tracker.push_current(HOST_SESSION, "subagent-invocation-id")

        adapter.handle_hook_event(
            _event(_post_tool_payload("runSubagent", {"agentName": "qa-engineer"}))
        )

        assert runner.list_functions() == ["check-step-postconditions"]
        assert runner.calls[0].inquiry == {"sessionId": TURN_SESSION, "parentSessionId": None}
        assert_valid_inquiry("check-step-postconditions", runner.calls[0].inquiry)

    def test_blocks_the_orchestrator_when_the_step_did_not_deliver(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H6, invariant 2: the block reason addresses the ORCHESTRATOR
        (re-resolution), never a user-facing verdict.
        """
        runner = FakeCommandRunner(
            reports=queue_reports(
                check_step_postconditions=[
                    build_report(
                        "check-step-postconditions",
                        "fail",
                        conditionChecks=[
                            {
                                "condition": {"kind": "postcondition", "slug": "report_exists"},
                                "outcome": "fail",
                                "failureMessage": "no artifact matches 'review-report'",
                            }
                        ],
                    )
                ]
            )
        )
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_post_tool_payload("runSubagent", {"agentName": "qa-engineer"}))
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["decision"] == "block"
        assert "re-resolve" in rendered["hookSpecificOutput"]["additionalContext"]

    def test_closes_the_step_session_and_pops_it_on_subagent_stop(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdin, assert_valid_inquiry
    ) -> None:
        """Adapter spec H7, case 1: the ending session is `sanitized(agent_id)` directly;
        function 11 closes it and the adapter then pops, restoring the dispatching
        orchestrator session as current.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)
        tracker.push_current(HOST_SESSION, "subagent-invocation-id")
        payload = assert_valid_stdin(
            {
                "timestamp": TIMESTAMP,
                "hook_event_name": "SubagentStop",
                "session_id": HOST_SESSION,
                "agent_id": "subagent-invocation-id",
            }
        )

        decision = adapter.handle_hook_event(_event(payload))

        assert runner.list_functions() == ["end-session"]
        assert runner.calls[0].inquiry == {"sessionId": "subagent-invocation-id"}
        assert_valid_inquiry("end-session", runner.calls[0].inquiry)
        assert tracker.resolve_current(HOST_SESSION) == TURN_SESSION
        assert (decision.exit_code, decision.stdout) == (0, "")

    def test_never_pops_the_dispatching_session_for_a_foreign_subagent_stop(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H7 / H1, invariant 3: a foreign subagent was never pushed, so its
        stop must neither close a session nor pop the orchestrator's own out from under
        its next mediated call.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                {
                    "timestamp": TIMESTAMP,
                    "hook_event_name": "SubagentStop",
                    "session_id": HOST_SESSION,
                    "agent_id": "foreign-subagent",
                }
            )
        )

        assert runner.calls == []
        assert tracker.resolve_current(HOST_SESSION) == TURN_SESSION
        assert (decision.exit_code, decision.stdout) == (0, "")

    def test_closes_the_resolved_turn_session_and_clears_the_stack_on_stop(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H7, case 2: `Stop` closes the session `resolve_current` returns —
        never the raw `session_id` — then CLEARS the stack, so a later firing under a
        non-framework agent resolves to None (the correct C7 pass-through).
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                {
                    "timestamp": TIMESTAMP,
                    "hook_event_name": "Stop",
                    "session_id": HOST_SESSION,
                    "stop_hook_active": False,
                }
            )
        )

        assert runner.calls[0].inquiry == {"sessionId": TURN_SESSION}
        assert tracker.resolve_current(HOST_SESSION) is None
        assert (decision.exit_code, decision.stdout) == (0, "")

    def test_invokes_nothing_when_the_ending_session_was_never_registered(
        self, make_adapter
    ) -> None:
        """Adapter spec H7: if resolution finds no current session, no harness function is
        invoked — pass-through, as elsewhere in this binding.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)

        decision = adapter.handle_hook_event(
            _event(
                {
                    "timestamp": TIMESTAMP,
                    "hook_event_name": "Stop",
                    "session_id": HOST_SESSION,
                }
            )
        )

        assert runner.calls == []
        assert (decision.exit_code, decision.stdout) == (0, "")

    def test_never_surfaces_a_closure_failure_to_the_host(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H7, Out / invariant 1: `end-session`'s outcome is never surfaced,
        success or error alike — best-effort closure has no host-visible effect.
        """
        runner = FakeCommandRunner(failure=RuntimeError("harness command crashed"))
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                {"timestamp": TIMESTAMP, "hook_event_name": "Stop", "session_id": HOST_SESSION}
            )
        )

        assert (decision.exit_code, decision.stdout) == (0, "")

    def test_leaves_an_unclassified_firing_completely_alone(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec — Boundary binding (C7): any firing matching no declared class is
        passed through: exit 0, empty stdout, NO journal entry.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("read_file", {"filePath": "portfolio/a.md"}))
        )

        assert (decision.exit_code, decision.stdout) == (0, "")
        assert runner.calls == []

    def test_denies_a_dispatch_when_the_harness_command_raises(
        self, make_adapter, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H2, invariant 1: deny-by-default covers the adapter's own failure
        to obtain an outcome, not merely a `fail` report.
        """
        runner = FakeCommandRunner(failure=RuntimeError("harness command crashed"))
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, TURN_SESSION)

        decision = adapter.handle_hook_event(
            _event(_pre_tool_payload("runSubagent", {"agentName": "qa-engineer"}))
        )

        assert assert_valid_stdout(decision.stdout)["hookSpecificOutput"][
            "permissionDecision"
        ] == "deny"

    def test_denies_a_mediated_command_when_the_tracker_record_is_unreadable(
        self, make_adapter, tmp_path: Path, tracker: SessionTracker, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, rule 3: an unresolvable current session denies — the adapter
        never stamps a session it cannot resolve.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.clear_current(HOST_SESSION)

        decision = adapter.handle_hook_event(
            _event(
                _pre_tool_payload(
                    "run_in_terminal",
                    {"command": "python3 harness.py resolve-step-model --workflow v"},
                )
            )
        )

        assert assert_valid_stdout(decision.stdout)["hookSpecificOutput"][
            "permissionDecision"
        ] == "deny"

    def test_attributes_a_new_turn_to_its_own_session_after_a_dead_turn(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec — correlation scenario 6: a dead turn that left an open workflow
        instance is continued by the CORE's latest-open-instance deduction; the adapter
        only attributes the new turn to its own new session and carries no instance data.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, "chat-session-guid-t2026-07-11t09-00-00-000z")

        adapter.handle_hook_event(
            _event(_prompt_payload(), agent="value-management-officer")
        )

        assert runner.calls[0].inquiry["sessionId"] == TURN_SESSION
        assert set(runner.calls[0].inquiry) == {"agent", "sessionId", "parentSessionId"}

    def test_re_resolution_after_a_dangling_step_needs_no_adapter_state(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec — correlation scenario 7: a dead turn that left a step resolved
        with no outcome is recovered by re-resolution (function 3, invariant 7); the
        adapter's stack is reset by H0 and carries no dangling step of its own.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        tracker.reset_current(HOST_SESSION, "chat-session-guid-t2026-07-11t09-00-00-000z")
        tracker.push_current(HOST_SESSION, "dangling-step")

        adapter.handle_hook_event(
            _event(_prompt_payload(), agent="value-management-officer")
        )

        assert tracker.resolve_current(HOST_SESSION) == TURN_SESSION
        assert tracker.resolve_parent(HOST_SESSION) is None


class TestHookEntryPoint:
    """Adapter spec — Invocation plumbing, seam 2/3: `dispatch.sh` execs this entry."""

    def test_forwards_the_event_and_scoping_agent_to_the_adapter(
        self, make_adapter, tracker: SessionTracker
    ) -> None:
        """Adapter spec H0, Registration: the scoping agent slug arrives as a trailing
        dispatch ARGUMENT (host-fixed), never in the payload.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        stdin_payload = json.dumps(_prompt_payload())
        stdout: list[str] = []

        exit_code = run_hook_entry(
            argv=["hook", "--event", "UserPromptSubmit", "--agent", "value-management-officer"],
            stdin_text=stdin_payload,
            write_stdout=stdout.append,
            build_adapter=lambda: adapter,
        )

        assert exit_code == 0
        assert runner.calls[0].inquiry["agent"] == "value-management-officer"
        assert json.loads(stdout[0])["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

    def test_writes_nothing_for_a_pass_through(self, make_adapter) -> None:
        """Adapter spec — Boundary binding (C7): pass-through is exit 0 with EMPTY stdout,
        which the seam-4 contract deliberately does not govern.
        """
        runner = FakeCommandRunner()
        adapter = make_adapter(runner)
        stdout: list[str] = []

        exit_code = run_hook_entry(
            argv=["hook", "--event", "PreToolUse"],
            stdin_text=json.dumps(_pre_tool_payload("read_file", {"filePath": "a.md"})),
            write_stdout=stdout.append,
            build_adapter=lambda: adapter,
        )

        assert (exit_code, stdout) == (0, [])
