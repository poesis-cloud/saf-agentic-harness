"""H0–H7 over the observed Copilot app event shapes, through a fake command runner."""

from __future__ import annotations

import pytest

from cli_rig import (
    CWD,
    HOST,
    ORCHESTRATOR,
    STEP_ACTOR,
    SUBAGENT,
    TURN_SESSION,
    WORKSPACE,
    Rig,
    build_error_report,
    build_report,
    post_tool_use,
    pre_tool_use,
)
from harness_stub import build_failing_condition_check

ARTIFACT = WORKSPACE + r"\portfolio\art\a1\f1.feature.md"
# Function 10, invariant 5: a concluded step outcome carries its delivery verdict.
DELIVERED = {
    "artifact": "feature",
    "artifactPath": "portfolio/art/a1/f1.feature.md",
    "outcome": "pass",
}


def _denied(decision) -> str:
    assert decision is not None and decision["permissionDecision"] == "deny"
    return decision["permissionDecisionReason"]


# --- sessionStart: stateless harness line ------------------------------------------


def test_session_start_announces_the_harness_line_and_records_nothing(rig: Rig) -> None:
    decision = rig.fire("sessionStart", {"source": "new", "sessionId": HOST, "cwd": CWD})
    context = decision["additionalContext"]
    assert "resolve-step --workflow-slug <workflow-slug>" in context
    assert "resolve-step-model" in context
    assert WORKSPACE in context and CWD in context
    assert rig.runner.calls == [] and rig.tracker.read(HOST) is None


# --- H0 ------------------------------------------------------------------------------


def test_h0_registers_the_orchestrator_turn_without_injecting(rig: Rig) -> None:
    decision = rig.fire(
        "userPromptSubmitted",
        {"sessionId": HOST, "prompt": "go", "timestamp": 1793784681555, "cwd": CWD},
        ORCHESTRATOR,
    )
    assert decision is None
    (inquiry,) = rig.runner.inquiries("start-session")
    assert inquiry == {"agent": ORCHESTRATOR, "sessionId": TURN_SESSION, "parentSessionId": None}
    assert rig.tracker.read(HOST).session_id == TURN_SESSION


def test_h0_injects_context_once_into_the_transformed_prompt(rig: Rig) -> None:
    rig.open_turn()
    payload = {
        "sessionId": HOST,
        "prompt": "go",
        "transformedPrompt": "<current_datetime>now</current_datetime> go",
        "timestamp": 1793784681559,
        "cwd": CWD,
    }
    decision = rig.fire("userPromptTransformed", payload, ORCHESTRATOR)
    prompt = decision["modifiedTransformedPrompt"]
    assert "Handle reports carefully." in prompt and "code-review" in prompt
    assert "resolve-step --workflow-slug" in prompt
    assert prompt.endswith(payload["transformedPrompt"])
    assert rig.tracker.read(HOST).context_injected
    assert rig.fire("userPromptTransformed", payload, ORCHESTRATOR) is None


def test_spurious_title_sessions_create_no_state(rig: Rig) -> None:
    """The app's background summarizers fire the GLOBAL hooks only — never with --agent."""
    other = "a33e97c0-42a8-4084-a338-b88f1f48dadb"
    for event, payload in (
        ("sessionStart", {"source": "new", "sessionId": other, "cwd": CWD}),
        ("userPromptSubmitted", {"sessionId": other, "prompt": "Summarize this assistant message"}),
        ("preToolUse", pre_tool_use(("create", {"path": ARTIFACT}), host=other)),
        ("agentStop", {"sessionId": other, "stopReason": "end_turn"}),
        ("sessionEnd", {"sessionId": other, "reason": "user_exit"}),
    ):
        decision = rig.fire(event, payload)
        assert decision is None or "additionalContext" in decision
    assert rig.runner.calls == []
    assert rig.tracker.read(other) is None


def test_h0_not_applicable_leaves_the_session_ungoverned(rig: Rig) -> None:
    rig.runner.script["start-session"] = build_report("start-session", "not-applicable")
    rig.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go"}, ORCHESTRATOR)
    assert rig.tracker.read(HOST) is None


# --- H1 ------------------------------------------------------------------------------


def test_h1_starts_the_step_under_the_dispatcher_and_injects_its_context(rig: Rig) -> None:
    rig.open_turn()
    decision = rig.open_step()
    assert "Handle reports carefully." in decision["additionalContext"]
    inquiry = rig.runner.inquiries("start-session")[-1]
    assert inquiry == {"agent": STEP_ACTOR, "sessionId": SUBAGENT, "parentSessionId": TURN_SESSION}
    record = rig.tracker.read(SUBAGENT)
    assert record.parent_session_id == TURN_SESSION and record.parent_host_id == HOST


def test_h1_inlines_a_step_context_larger_than_the_post_tool_use_cap(rig: Rig, tmp_path) -> None:
    """The 10 KB cap is `postToolUse`'s (hooks reference); a `subagentStart` context is
    bounded only by the 10 MiB hook-output limit. So H1 delivers the whole context, untruncated,
    and only then marks the step's context injected."""
    from copilot_cli_adapter.renderer import ADDITIONAL_CONTEXT_LIMIT

    body = "Handle reports carefully. " * (2 * ADDITIONAL_CONTEXT_LIMIT // 26)
    (tmp_path / "instructions" / "reports-handling.instructions.md").write_text(
        body + "END-OF-INSTRUCTION\n", encoding="utf-8"
    )
    rig.open_turn()
    context = rig.open_step()["additionalContext"]
    assert len(context.encode("utf-8")) > ADDITIONAL_CONTEXT_LIMIT
    assert "END-OF-INSTRUCTION" in context and "truncated" not in context
    assert rig.tracker.read(SUBAGENT).context_injected


def test_h1_falls_back_to_load_directives_past_the_hook_output_limit(
    rig: Rig, tmp_path, monkeypatch
) -> None:
    from copilot_cli_adapter import renderer

    (tmp_path / "instructions" / "reports-handling.instructions.md").write_text(
        "x" * 4000 + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(renderer, "HOOK_OUTPUT_LIMIT", 3500)
    rig.open_turn()
    context = rig.open_step()["additionalContext"]
    assert "Read `" in context and "reports-handling.instructions.md" in context
    assert "x" * 100 not in context
    assert rig.tracker.read(SUBAGENT).context_injected


def test_h1_marks_nothing_injected_when_even_directives_cannot_fit(
    rig: Rig, monkeypatch
) -> None:
    from copilot_cli_adapter import renderer

    monkeypatch.setattr(renderer, "HOOK_OUTPUT_LIMIT", 200)
    rig.open_turn()
    assert rig.open_step() is None
    assert rig.tracker.read(SUBAGENT) is not None
    assert not rig.tracker.read(SUBAGENT).context_injected
    assert any("exceeds what the runtime accepts" in line for line in rig.diagnostics)


def test_the_late_context_is_marked_injected_only_when_delivered_whole(
    rig: Rig, monkeypatch
) -> None:
    from copilot_cli_adapter import renderer

    rig.open_turn()
    monkeypatch.setattr(renderer, "ADDITIONAL_CONTEXT_LIMIT", 300)
    decision = rig.fire("postToolUse", post_tool_use("view", {"path": "C:\\a"}))
    assert "truncated" in decision["additionalContext"]
    assert not rig.tracker.read(HOST).context_injected
    monkeypatch.setattr(renderer, "ADDITIONAL_CONTEXT_LIMIT", 10_000)
    decision = rig.fire("postToolUse", post_tool_use("view", {"path": "C:\\a"}))
    assert "truncated" not in decision["additionalContext"]
    assert rig.tracker.read(HOST).context_injected


def test_h1_under_an_ungoverned_parent_is_ignored(rig: Rig) -> None:
    assert rig.open_step() is None
    assert rig.runner.calls == []


# --- H2 ------------------------------------------------------------------------------


def test_h2_passes_a_dispatch_whose_preconditions_pass(rig: Rig) -> None:
    rig.open_turn()
    task = {"agent_type": STEP_ACTOR, "name": "s", "description": "d", "prompt": "p"}
    assert rig.fire("preToolUse", pre_tool_use(("task", task))) is None
    assert rig.runner.inquiries("check-step-preconditions") == [
        {"sessionId": TURN_SESSION, "parentSessionId": None}
    ]


def test_h2_denies_a_dispatch_whose_preconditions_fail(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["check-step-preconditions"] = build_report(
        "check-step-preconditions",
        "fail",
        conditionChecks=[build_failing_condition_check("feature-exists", "no feature yet")],
    )
    reason = _denied(rig.fire("preToolUse", pre_tool_use(("task", {"agent_type": STEP_ACTOR}))))
    assert "no feature yet" in reason


def test_h2_denies_background_dispatch(rig: Rig) -> None:
    rig.open_turn()
    task = {"agent_type": STEP_ACTOR, "prompt": "p", "mode": "background"}
    assert "background" in _denied(rig.fire("preToolUse", pre_tool_use(("task", task))))


# --- H3 ------------------------------------------------------------------------------


def test_h3_authorizes_a_windows_write_as_a_workspace_relative_path(rig: Rig) -> None:
    rig.open_turn()
    assert rig.fire("preToolUse", pre_tool_use(("create", {"path": ARTIFACT, "file_text": "x"}))) is None
    (inquiry,) = rig.runner.inquiries("check-step-authorization")
    assert inquiry["artifactPath"] == "portfolio/art/a1/f1.feature.md"
    assert inquiry["action"] == "create"
    assert inquiry["sessionId"] == TURN_SESSION


def test_h3_denies_the_whole_batch_when_any_write_is_unauthorized(rig: Rig) -> None:
    rig.open_turn()
    allowed = rig.runner.script["check-step-authorization"]
    denied = build_report(
        "check-step-authorization",
        "denied",
        authorization={
            "actor": ORCHESTRATOR,
            "artifactPath": "portfolio/b.md",
            "action": "update",
            "resource": "epic",
            "failureMessage": "missing privilege: update epic",
        },
    )
    rig.runner.script["check-step-authorization"] = [allowed, denied]
    reason = _denied(
        rig.fire(
            "preToolUse",
            pre_tool_use(
                ("create", {"path": ARTIFACT, "file_text": "x"}),
                ("view", {"path": ARTIFACT}),
                ("edit", {"path": WORKSPACE + r"\portfolio\b.md", "old_str": "a", "new_str": "b"}),
            ),
        )
    )
    assert "missing privilege" in reason
    assert len(rig.runner.inquiries("check-step-authorization")) == 2


def test_h3_fails_closed_on_unextractable_targets(rig: Rig) -> None:
    rig.open_turn()
    for call in (
        ("create", {"file_text": "no path"}),
        ("apply_patch", {"input": "this is not a patch"}),
        ("edit", {"path": ""}),
    ):
        assert "fail closed" in _denied(rig.fire("preToolUse", pre_tool_use(call)))
    single = {"sessionId": HOST, "toolName": "create", "toolArgs": "{not json"}
    assert _denied(rig.fire("preToolUse", single))
    assert rig.runner.inquiries("check-step-authorization") == []


def test_h3_denies_a_patch_whose_header_path_is_not_verbatim(rig: Rig) -> None:
    """`portfolio/a.md ` (trailing space) is a different file from the one a trimmed path
    would authorize: the patch is unextractable and denied, no harness call runs."""
    rig.open_turn()
    patch = f"*** Begin Patch\n*** Add File: {WORKSPACE}\\portfolio\\a.md \n+x\n*** End Patch\n"
    reason = _denied(rig.fire("preToolUse", pre_tool_use(("apply_patch", {"input": patch}))))
    assert "fail closed" in reason
    assert rig.runner.inquiries("check-step-authorization") == []


def test_h3_authorizes_every_apply_patch_target(rig: Rig) -> None:
    rig.open_turn()
    patch = (
        "*** Begin Patch\n"
        f"*** Add File: {WORKSPACE}\\portfolio\\n.md\n+x\n"
        f"*** Delete File: {WORKSPACE}\\portfolio\\o.md\n"
        "*** End Patch\n"
    )
    assert rig.fire("preToolUse", pre_tool_use(("apply_patch", {"input": patch}))) is None
    inquiries = rig.runner.inquiries("check-step-authorization")
    assert [(i["artifactPath"], i["action"]) for i in inquiries] == [
        ("portfolio/n.md", "create"),
        ("portfolio/o.md", "delete"),
    ]


def test_h3_denies_when_the_harness_is_unavailable(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["check-step-authorization"] = RuntimeError("Traceback: secret detail")
    reason = _denied(rig.fire("preToolUse", pre_tool_use(("create", {"path": ARTIFACT}))))
    assert "unavailable" in reason and "Traceback" not in reason
    assert any("secret detail" in line for line in rig.diagnostics)


def test_malformed_pre_tool_use_in_a_governed_session_is_denied(rig: Rig) -> None:
    rig.open_turn()
    _denied(rig.fire("preToolUse", {"sessionId": HOST, "toolCalls": [{"args": {}}]}))
    _denied(rig.fire("preToolUse", {"cwd": CWD, "toolCalls": []}))


def test_read_only_and_human_gate_tools_pass_in_a_governed_session(rig: Rig) -> None:
    """`skill` loads a skill, `ask_user` is the orchestrators' human gate — never denied."""
    rig.open_turn()
    for call in (
        ("skill", {"skill": "program-pi-planning"}),
        ("ask_user", {"question": "Approve the PI objectives?", "choices": ["Yes", "No"]}),
        ("view", {"path": ARTIFACT}),
        ("grep", {"pattern": "x"}),
        ("glob", {"pattern": "**/*.md"}),
    ):
        assert rig.fire("preToolUse", pre_tool_use(call)) is None
    assert rig.fire(
        "preToolUse",
        pre_tool_use(("skill", {"skill": "team-verification"}), ("ask_user", {"question": "ok?"})),
    ) is None
    assert rig.runner.inquiries("check-step-authorization") == []


# --- I9 guarded shell ------------------------------------------------------------------


def test_shell_commands_naming_the_workspace_are_denied(rig: Rig) -> None:
    rig.open_turn()
    for command in (
        r"Set-Content -Path workspace\portfolio\a.md -Value x",
        f"Remove-Item '{WORKSPACE}\\portfolio\\a.md'",
        "echo x > workspace/portfolio/a.md",
        "Get-Content logs\\s.log.jsonl",
    ):
        reason = _denied(rig.fire("preToolUse", pre_tool_use(("powershell", {"command": command}))))
        assert "governed workspace" in reason
    assert rig.fire("preToolUse", pre_tool_use(("powershell", {"command": "git status --short"}))) is None


# --- H5 ------------------------------------------------------------------------------


def test_h5_validates_the_landed_write(rig: Rig) -> None:
    rig.open_turn()
    rig.tracker.write(HOST, rig.tracker.read(HOST).with_context_injected())
    assert rig.fire("postToolUse", post_tool_use("create", {"path": ARTIFACT})) is None
    (inquiry,) = rig.runner.inquiries("check-step-artifact")
    assert inquiry["artifactPaths"] == ["portfolio/art/a1/f1.feature.md"]


def test_h5_replaces_the_result_of_a_rejected_write(rig: Rig) -> None:
    rig.open_turn()
    rig.tracker.write(HOST, rig.tracker.read(HOST).with_context_injected())
    rig.runner.script["check-step-artifact"] = build_report(
        "check-step-artifact",
        "reverted",
        artifactChecks=[
            {
                "artifactPath": "portfolio/art/a1/f1.feature.md",
                "failureMessage": "frontmatter.status is required",
                "revert": {"action": "deleted"},
            }
        ],
    )
    decision = rig.fire("postToolUse", post_tool_use("create", {"path": ARTIFACT}))
    assert decision["modifiedResult"]["resultType"] == "success"
    assert "WRITE REJECTED" in decision["modifiedResult"]["textResultForLlm"]
    assert "frontmatter.status is required" in decision["additionalContext"]


def test_h5_never_claims_a_restore_for_an_error_outcome(rig: Rig) -> None:
    """`log-unreadable` (or any error) is returned before artifact processing: the file may
    still be modified, so the result names the error and claims no rollback."""
    rig.open_turn()
    rig.tracker.write(HOST, rig.tracker.read(HOST).with_context_injected())
    rig.runner.script["check-step-artifact"] = build_error_report(
        "check-step-artifact",
        "system-error",
        "log-unreadable",
        "The session log cannot be read.",
    )
    decision = rig.fire("postToolUse", post_tool_use("create", {"path": ARTIFACT}))
    text = decision["modifiedResult"]["textResultForLlm"]
    assert "WRITE OUTCOME UNKNOWN" in text and "log-unreadable" in text
    assert "WRITE REJECTED" not in text
    assert "restored from HEAD" not in decision["additionalContext"]
    assert "log-unreadable" in decision["additionalContext"]


def test_h5_leaves_the_commit_state_open_for_an_error_after_the_commit(rig: Rig) -> None:
    """`log-append-failed` arrives after the harness committed: the result must claim
    neither a commit nor a restore, and direct the actor to inspect before retrying."""
    rig.open_turn()
    rig.tracker.write(HOST, rig.tracker.read(HOST).with_context_injected())
    rig.runner.script["check-step-artifact"] = build_error_report(
        "check-step-artifact",
        "system-error",
        "log-append-failed",
        "The session log could not be appended.",
    )
    decision = rig.fire("postToolUse", post_tool_use("create", {"path": ARTIFACT}))
    context = decision["additionalContext"]
    assert "UNKNOWN" in context and "may already be committed" in context
    assert "Inspect the artifact" in context
    for claim in ("neither committed", "was not committed", "restored from HEAD", "discarded)"):
        assert claim not in context
    assert "OUTCOME UNKNOWN" in decision["modifiedResult"]["textResultForLlm"]


def test_a_failed_write_is_not_validated(rig: Rig) -> None:
    rig.open_turn()
    rig.tracker.write(HOST, rig.tracker.read(HOST).with_context_injected())
    rig.fire("postToolUseFailure", post_tool_use("edit", {"path": ARTIFACT}, event_failed=True))
    assert rig.runner.inquiries("check-step-artifact") == []


# --- H6 ------------------------------------------------------------------------------


def test_h6_evaluates_the_returned_step_in_the_dispatcher(rig: Rig) -> None:
    rig.open_turn()
    rig.tracker.write(HOST, rig.tracker.read(HOST).with_context_injected())
    rig.runner.script["check-step-postconditions"] = build_report(
        "check-step-postconditions",
        "fail",
        conditionChecks=[build_failing_condition_check("feature-written", "no feature file")],
        artifactDelivery=DELIVERED,
    )
    decision = rig.fire("postToolUse", post_tool_use("task", {"agent_type": STEP_ACTOR}))
    assert "no feature file" in decision["additionalContext"]
    assert rig.runner.inquiries("check-step-postconditions") == [
        {"sessionId": TURN_SESSION, "parentSessionId": None}
    ]


def test_h6_shows_the_reason_of_a_delivery_only_failure(rig: Rig) -> None:
    rig.open_turn()
    rig.tracker.write(HOST, rig.tracker.read(HOST).with_context_injected())
    rig.runner.script["check-step-postconditions"] = build_report(
        "check-step-postconditions",
        "fail",
        conditionChecks=[],
        artifactDelivery={
            "artifact": "feature",
            "artifactPath": None,
            "outcome": "fail",
            "failureMessage": "step 'draft' delivered no 'feature' instance",
        },
    )
    decision = rig.fire("postToolUse", post_tool_use("task", {"agent_type": STEP_ACTOR}))
    assert (
        "[artifact delivery] fail — step 'draft' delivered no 'feature' instance"
        in decision["additionalContext"]
    )


def test_late_context_reaches_a_session_no_prompt_hook_could_inject(rig: Rig) -> None:
    rig.open_turn()
    decision = rig.fire("postToolUse", post_tool_use("view", {"path": ARTIFACT}))
    context = decision["additionalContext"]
    assert "reports-handling.instructions.md` before acting" in context
    assert "Handle reports carefully." not in context  # directives, not inlined text
    assert rig.tracker.read(HOST).context_injected


# --- H7 ------------------------------------------------------------------------------


def test_h7_closes_the_step_then_the_turn(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.fire("agentStop", {"sessionId": SUBAGENT, "agentId": SUBAGENT, "parentAgentId": HOST})
    rig.fire("subagentStop", {"sessionId": HOST, "agentId": SUBAGENT, "agentType": STEP_ACTOR})
    assert rig.tracker.read(SUBAGENT) is None
    rig.fire("agentStop", {"sessionId": HOST, "stopReason": "end_turn"})
    assert rig.tracker.read(HOST) is None
    assert [i["sessionId"] for i in rig.runner.inquiries("end-session")] == [SUBAGENT, TURN_SESSION]


def test_session_end_closes_every_session_left_open(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.fire("sessionEnd", {"sessionId": HOST, "reason": "user_exit"})
    assert rig.tracker.read(HOST) is None and rig.tracker.read(SUBAGENT) is None
    assert {i["sessionId"] for i in rig.runner.inquiries("end-session")} == {SUBAGENT, TURN_SESSION}


@pytest.mark.parametrize(
    "answer",
    [
        build_error_report("start-session"),
        build_error_report("start-session", code="session-instance-bound", message="already bound"),
        RuntimeError("harness unavailable"),
    ],
    ids=["error-report", "session-instance-bound", "no-report"],
)
def test_a_failed_turn_registration_fails_closed(rig: Rig, answer) -> None:
    """Only an explicit `not-applicable` leaves a session ungoverned. A failed
    `start-session` opens no harness session, but the host session's writes, dispatches and
    harness requests are denied instead of bypassing authorization."""
    rig.runner.script["start-session"] = answer
    rig.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go"}, ORCHESTRATOR)
    record = rig.tracker.read(HOST)
    assert record is not None and not record.registered
    for call in (
        ("create", {"path": ARTIFACT, "file_text": "x"}),
        ("task", {"agent_type": STEP_ACTOR, "prompt": "p"}),
        ("powershell", {"command": "python C:\\Users\\cleme\\saf-agentic-harness\\harness.py resolve-step-model"}),
    ):
        reason = _denied(rig.fire("preToolUse", pre_tool_use(call)))
        assert "not registered with the harness" in reason and "fail closed" in reason
    assert rig.fire("preToolUse", pre_tool_use(("view", {"path": ARTIFACT}))) is None
    called = set(rig.runner.functions()) - {"start-session"}
    assert called == set()


def test_a_not_applicable_turn_stays_ungoverned(rig: Rig) -> None:
    rig.runner.script["start-session"] = build_report("start-session", "not-applicable")
    rig.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go"}, ORCHESTRATOR)
    assert rig.tracker.read(HOST) is None
    assert rig.fire("preToolUse", pre_tool_use(("create", {"path": ARTIFACT}))) is None


def test_a_failed_turn_registration_is_retried_at_the_next_prompt(rig: Rig) -> None:
    started = rig.runner.script["start-session"]
    rig.runner.script["start-session"] = build_error_report("start-session")
    rig.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 1}, ORCHESTRATOR)
    assert not rig.tracker.read(HOST).registered
    rig.runner.script["start-session"] = started
    rig.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go", "timestamp": 2}, ORCHESTRATOR)
    record = rig.tracker.read(HOST)
    assert record.registered and record.session_id == f"{HOST}-t2"
    assert rig.fire("preToolUse", pre_tool_use(("create", {"path": ARTIFACT}))) is None


@pytest.mark.parametrize(
    "answer",
    [
        build_error_report("start-session", code="step-session-bound", message="bound"),
        RuntimeError("harness unavailable"),
    ],
    ids=["step-session-bound", "no-report"],
)
def test_a_failed_step_registration_fails_closed(rig: Rig, answer) -> None:
    rig.open_turn()
    rig.runner.script["start-session"] = answer
    assert rig.open_step() is None
    record = rig.tracker.read(SUBAGENT)
    assert record is not None and not record.registered
    reason = _denied(rig.fire("preToolUse", pre_tool_use(("create", {"path": ARTIFACT}), host=SUBAGENT)))
    assert "not registered with the harness" in reason
    assert rig.runner.inquiries("check-step-authorization") == []
    rig.fire("postToolUse", post_tool_use("create", {"path": ARTIFACT}, host=SUBAGENT))
    assert rig.runner.inquiries("check-step-artifact") == []
    rig.fire("subagentStop", {"sessionId": HOST, "agentId": SUBAGENT, "agentType": STEP_ACTOR})
    assert rig.tracker.read(SUBAGENT) is None
    assert SUBAGENT not in {i["sessionId"] for i in rig.runner.inquiries("end-session")}


def test_a_not_applicable_step_stays_ungoverned(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["start-session"] = build_report("start-session", "not-applicable")
    assert rig.open_step() is None
    assert rig.tracker.read(SUBAGENT) is None


def test_a_failed_turn_closes_without_ending_a_harness_session(rig: Rig) -> None:
    rig.runner.script["start-session"] = build_error_report("start-session")
    rig.fire("userPromptSubmitted", {"sessionId": HOST, "prompt": "go"}, ORCHESTRATOR)
    rig.fire("sessionEnd", {"sessionId": HOST, "reason": "complete"})
    assert rig.tracker.read(HOST) is None
    assert rig.runner.inquiries("end-session") == []


def test_h0_binds_a_turn_once_across_both_prompt_events(rig: Rig) -> None:
    rig.open_turn()
    rig.fire(
        "userPromptTransformed",
        {"sessionId": HOST, "prompt": "go", "transformedPrompt": "go", "timestamp": 2},
        ORCHESTRATOR,
    )
    assert len(rig.runner.inquiries("start-session")) == 1


def test_h3_denies_on_a_system_error_report(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["check-step-authorization"] = build_error_report(
        "check-step-authorization",
        status="system-error",
        code="log-unreadable",
        message="The session log cannot be read.",
    )
    reason = _denied(rig.fire("preToolUse", pre_tool_use(("create", {"path": ARTIFACT, "file_text": "x"}))))
    assert "system-error" in reason and "log-unreadable" in reason
