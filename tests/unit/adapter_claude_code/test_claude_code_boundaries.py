"""`claude-code` adapter: every boundary (H0–H7) over a fake command API."""

from __future__ import annotations

import json
from pathlib import Path

from claude_rig import (
    HARNESS,
    ORCHESTRATOR,
    OTHER_WORKER,
    PLUGIN,
    SESSION,
    STDIN_CONTRACT,
    STEP_ACTOR,
    TURN_SESSION,
    WORKER,
    Rig,
    assert_matches_contract,
    build_error_report,
    build_report,
    captured_payloads,
    dispatch,
    event,
    in_worker,
    main_thread,
)
from claude_code_adapter import renderer
from harness_stub import build_failing_condition_check
from claude_code_adapter.core import HARNESS_RESULT
from saf_host_adapter.payload import HookInput


def _specific(decision) -> dict:
    return dict((decision or {}).get("hookSpecificOutput") or {})


def _denied(decision) -> bool:
    return _specific(decision).get("permissionDecision") == "deny"


def test_every_captured_payload_meets_the_stdin_contract_and_reads_its_session(binding) -> None:
    for payload in captured_payloads():
        assert_matches_contract(STDIN_CONTRACT, payload)
        hook = HookInput(event=payload["hook_event_name"], payload=payload, binding=binding,
                         dispatcher_from_session=False)
        assert hook.root_host_id == payload["session_id"]
        assert hook.host_id == payload.get("agent_id", payload["session_id"])
        assert hook.dispatcher_host_id is None


def test_the_captured_session_replays_silently_when_ungoverned(rig: Rig) -> None:
    """No orchestrator of a framework ever opened a turn: nothing runs, everything passes,
    except the stateless announce."""
    rig.runner.script["start-session"] = build_report("start-session", "not-applicable")
    for payload in captured_payloads():
        decision = rig.fire(payload)
        if payload["hook_event_name"] == "SessionStart":
            assert "SAFe agentic harness" in _specific(decision)["additionalContext"]
        else:
            assert decision is None, payload
    assert set(rig.runner.functions()) <= {"start-session"}
    assert rig.tracker.read(SESSION) is None


def test_h0_opens_the_turn_from_the_conversations_agent_and_injects_its_context(rig: Rig) -> None:
    decision = rig.open_turn()
    (inquiry,) = rig.runner.inquiries("start-session")
    assert inquiry == {"agent": ORCHESTRATOR, "sessionId": TURN_SESSION, "parentSessionId": None}
    context = _specific(decision)["additionalContext"]
    assert "Handle reports carefully." in context and HARNESS in context
    assert rig.tracker.read(SESSION).context_injected
    assert rig.fire(main_thread("UserPromptSubmit", prompt_id="next", prompt="again")) is None


def test_h0_ignores_a_conversation_without_a_selected_agent(rig: Rig) -> None:
    assert rig.fire(event("UserPromptSubmit", prompt_id="p", prompt="go")) is None
    assert rig.runner.calls == []


def test_h0_falls_back_to_load_directives_when_the_context_is_too_large(
    rig: Rig, monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "instructions" / "reports-handling.instructions.md").write_text(
        "Handle reports carefully.\n" + "x" * 4000, encoding="utf-8"
    )
    inline = _specific(rig.open_turn())["additionalContext"]
    rig.fire(main_thread("Stop", stop_hook_active=False))
    limit = len(inline.encode("utf-8")) - 1
    monkeypatch.setattr(renderer, "ADDITIONAL_CONTEXT_LIMIT", limit)
    context = _specific(rig.fire(main_thread("UserPromptSubmit", prompt_id="p2", prompt="go")))[
        "additionalContext"
    ]
    assert "Read `" in context and "Handle reports carefully." not in context
    assert len(context.encode("utf-8")) <= limit


def test_h1_attributes_the_started_subagent_to_the_dispatch_it_correlates(rig: Rig) -> None:
    rig.open_turn()
    decision = rig.open_step()
    inquiry = rig.runner.inquiries("start-session")[-1]
    assert inquiry == {"agent": STEP_ACTOR, "sessionId": WORKER, "parentSessionId": TURN_SESSION}
    assert "SAFe agentic harness" in _specific(decision)["additionalContext"]
    record = rig.tracker.read(WORKER)
    assert record.parent_host_id == SESSION and record.registered
    assert rig.tracker.dispatcher_of(WORKER) == SESSION


def test_h1_leaves_a_subagent_no_seen_dispatch_names_ungoverned(rig: Rig) -> None:
    rig.open_turn()
    started = rig.fire(event("SubagentStart", agent_id=WORKER, agent_type=f"{PLUGIN}:{STEP_ACTOR}"))
    assert started is None and rig.tracker.read(WORKER) is None


def test_h1_refuses_a_subagent_whose_dispatcher_is_ambiguous(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    nested = in_worker("PreToolUse", tool_name="Agent", tool_use_id="toolu_n",
                       tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}", "prompt": "p",
                                   "description": "d", "run_in_background": False})
    assert not _denied(rig.fire(nested))
    assert rig.fire(dispatch("toolu_e")) is None
    rig.fire(event("SubagentStart", agent_id=OTHER_WORKER, agent_type=f"{PLUGIN}:{STEP_ACTOR}"))
    record = rig.tracker.read(OTHER_WORKER)
    assert record is not None and not record.registered
    assert "cannot be told apart" in record.registration_failure
    write = in_worker("PreToolUse", worker=OTHER_WORKER, tool_name="Write", tool_use_id="w",
                      tool_input={"file_path": "/home/user/acme-notes/workspace/a.md", "content": "x"})
    assert _denied(rig.fire(write))


def test_h2_denies_a_dispatch_that_may_run_in_the_background(rig: Rig) -> None:
    rig.open_turn()
    for flag in (None, True, "false"):
        decision = rig.fire(dispatch(run_in_background=flag))
        assert _denied(decision)
        assert "run_in_background" in _specific(decision)["permissionDecisionReason"]
    assert rig.fire(dispatch()) is None
    assert rig.runner.functions().count("check-step-preconditions") == 1


def test_h2_relays_failing_preconditions(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["check-step-preconditions"] = build_report(
        "check-step-preconditions", "fail",
        conditionChecks=[build_failing_condition_check("ready", "not ready")])
    assert "[ready] fail — not ready" in _specific(rig.fire(dispatch()))["permissionDecisionReason"]


def test_h3_resolves_an_upsert_write_by_whether_its_target_exists(rig: Rig, tmp_path: Path) -> None:
    rig.open_turn()
    rig.open_step()
    existing = tmp_path / "existing.md"
    existing.write_text("x", encoding="utf-8")
    for path in (existing, tmp_path / "missing.md"):
        payload = in_worker("PreToolUse", tool_name="Write", tool_use_id="w",
                            tool_input={"file_path": str(path), "content": "y"})
        assert rig.fire(payload) is None
    actions = [i["action"] for i in rig.runner.inquiries("check-step-authorization")]
    assert actions == ["update", "create"]
    edit = in_worker("PreToolUse", tool_name="Edit", tool_use_id="e",
                     tool_input={"file_path": str(existing), "old_string": "x", "new_string": "z"})
    rig.fire(edit)
    assert rig.runner.inquiries("check-step-authorization")[-1]["action"] == "update"


def test_h3_denies_an_unauthorized_write_and_a_write_without_a_path(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.runner.script["check-step-authorization"] = build_report(
        "check-step-authorization", "denied", authorization={
            "actor": STEP_ACTOR, "artifactPath": "a.md", "action": "create", "resource": "epic",
            "failureMessage": "not granted"})
    write = in_worker("PreToolUse", tool_name="Write", tool_use_id="w",
                      tool_input={"file_path": "/home/user/acme-notes/workspace/a.md", "content": "y"})
    assert "not granted" in _specific(rig.fire(write))["permissionDecisionReason"]
    pathless = in_worker("PreToolUse", tool_name="Write", tool_use_id="w2", tool_input={"content": "y"})
    assert "fail closed" in _specific(rig.fire(pathless))["permissionDecisionReason"]


def test_h4_runs_the_requested_function_for_the_observed_session(rig: Rig) -> None:
    rig.open_turn()
    request = main_thread("PreToolUse", tool_name="Bash", tool_use_id="r", tool_input={
        "command": f"python3 {HARNESS} resolve-step --workflow-slug strategic-themes",
        "description": "resolve"})
    reason = _specific(rig.fire(request))["permissionDecisionReason"]
    assert reason.startswith(f"{HARNESS_RESULT} (resolve-step)")
    assert json.loads(reason.split("\n\n", 1)[1])["outcome"]["status"] == "no-next-step"
    assert rig.runner.inquiries("resolve-step") == [
        {"workflowSlug": "strategic-themes", "sessionId": TURN_SESSION, "parentSessionId": None}
    ]


def test_i9_denies_a_shell_command_naming_the_workspace(rig: Rig) -> None:
    rig.open_turn()
    shell = main_thread("PreToolUse", tool_name="Bash", tool_use_id="s",
                        tool_input={"command": "echo x > /home/user/acme-notes/workspace/a.md"})
    assert "governed workspace path" in _specific(rig.fire(shell))["permissionDecisionReason"]


def test_h5_blocks_the_result_of_a_reverted_write(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.runner.script["check-step-artifact"] = build_report(
        "check-step-artifact", "reverted", artifactChecks=[{
            "artifactPath": "a.md", "failureMessage": "bad schema",
            "revert": {"action": "deleted"}}])
    landed = in_worker("PostToolUse", tool_name="Write", tool_use_id="w",
                       tool_input={"file_path": "/home/user/acme-notes/workspace/a.md", "content": "y"},
                       tool_response={"type": "create"})
    decision = rig.fire(landed)
    assert decision["decision"] == "block" and "WRITE REJECTED" in decision["reason"]
    assert "a.md deleted" in _specific(decision)["additionalContext"]


def test_h5_reports_an_error_outcome_as_unknown(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.runner.script["check-step-artifact"] = build_error_report("check-step-artifact")
    landed = in_worker("PostToolUse", tool_name="Edit", tool_use_id="e",
                       tool_input={"file_path": "/home/user/acme-notes/workspace/a.md",
                                   "old_string": "a", "new_string": "b"}, tool_response={})
    decision = rig.fire(landed)
    assert decision["decision"] == "block" and "WRITE OUTCOME UNKNOWN" in decision["reason"]


def test_h6_evaluates_the_bound_workers_return_in_the_dispatching_session(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.fire(in_worker("SubagentStop", stop_hook_active=False))
    # A stop can be vetoed by another hook: the worker stays governed until its dispatch returns.
    assert rig.tracker.read(WORKER).stopped
    assert rig.runner.inquiries("end-session") == []
    rig.runner.script["check-step-postconditions"] = build_report(
        "check-step-postconditions", "fail",
        conditionChecks=[build_failing_condition_check("owned", "wrong owner")],
        artifactDelivery={"artifact": "strategic-themes", "artifactPath": "a.md", "outcome": "pass"},
        promotion={"outcome": "withheld", "workspaceCommit": "0" * 40})
    returned = main_thread("PostToolUse", tool_name="Agent", tool_use_id="toolu_d",
                           tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}", "prompt": "p",
                                       "run_in_background": False},
                           tool_response={"status": "completed", "agentId": WORKER})
    context = _specific(rig.fire(returned))["additionalContext"]
    assert "[owned] fail — wrong owner" in context and "re-resolve" in context
    assert rig.runner.inquiries("check-step-postconditions") == [
        {"sessionId": TURN_SESSION, "parentSessionId": None}
    ]
    # The worker session ended at its dispatch's return, before the step was evaluated.
    functions = rig.runner.functions()
    assert functions.index("end-session") < functions.index("check-step-postconditions")
    assert rig.runner.inquiries("end-session") == [{"sessionId": WORKER}]
    assert rig.tracker.read(WORKER) is None


def test_h6_skips_the_return_of_a_refused_duplicate(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step(WORKER, "toolu_a")
    rig.runner.script["start-session"] = build_error_report(
        "start-session", code="worker-session-bound", message="bound")
    rig.open_step(OTHER_WORKER, "toolu_b")
    assert not rig.tracker.read(OTHER_WORKER).registered
    rig.fire(in_worker("SubagentStop", worker=OTHER_WORKER, stop_hook_active=False))
    returned = main_thread("PostToolUse", tool_name="Agent", tool_use_id="toolu_b",
                           tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}", "prompt": "p",
                                       "run_in_background": False},
                           tool_response={"status": "completed", "agentId": OTHER_WORKER})
    rig.fire(returned)
    assert "check-step-postconditions" not in rig.runner.functions()


def test_h7_closes_the_turn_and_every_descendant(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.fire(dispatch("toolu_pending"))
    rig.fire(event("SessionEnd", reason="other"))
    assert rig.tracker.read(SESSION) is None and rig.tracker.read(WORKER) is None
    assert {i["sessionId"] for i in rig.runner.inquiries("end-session")} == {WORKER, TURN_SESSION}
    assert rig.tracker.claim_pending_dispatch(SESSION, f"{PLUGIN}:{STEP_ACTOR}") is None


def test_stop_ends_the_turn_and_the_next_prompt_opens_a_new_one(rig: Rig) -> None:
    rig.open_turn()
    rig.fire(main_thread("Stop", stop_hook_active=False))
    assert rig.tracker.read(SESSION).stopped
    assert rig.runner.inquiries("end-session") == []
    rig.fire(main_thread("UserPromptSubmit", prompt_id="second", prompt="go on"))
    assert rig.runner.inquiries("end-session") == [{"sessionId": TURN_SESSION}]
    assert rig.runner.inquiries("start-session")[-1]["sessionId"] == f"{SESSION}-tsecond"
    assert not rig.tracker.read(SESSION).stopped


def test_an_agent_whose_stop_was_vetoed_stays_governed(rig: Rig) -> None:
    """Review of #42: another Stop/SubagentStop hook can veto the stop; the agent keeps
    working and every tool call it makes is still governed."""
    rig.open_turn()
    rig.open_step()
    rig.fire(in_worker("SubagentStop", stop_hook_active=False))
    rig.runner.script["check-step-authorization"] = build_report(
        "check-step-authorization", "denied", authorization={
            "actor": STEP_ACTOR, "artifactPath": "a.md", "action": "create", "resource": "epic",
            "failureMessage": "not granted"})
    write = in_worker("PreToolUse", tool_name="Write", tool_use_id="w2",
                      tool_input={"file_path": "/home/user/acme-notes/workspace/a.md", "content": "y"},
                      stop_hook_active=True)
    assert _denied(rig.fire(write))
    rig.fire(main_thread("Stop", stop_hook_active=False))
    own = main_thread("PreToolUse", tool_name="Write", tool_use_id="w3",
                      tool_input={"file_path": "/home/user/acme-notes/workspace/b.md", "content": "y"})
    assert _denied(rig.fire(own))
    assert rig.runner.inquiries("end-session") == []


def test_a_session_another_harness_version_opened_is_refused(rig: Rig) -> None:
    rig.open_turn()
    record = rig.tracker.read(SESSION)
    from dataclasses import replace

    rig.tracker.write(SESSION, replace(record, harness_version="0.9.0"))
    assert "plugin was updated" in _specific(rig.fire(dispatch()))["permissionDecisionReason"]
    assert rig.fire(event("SubagentStart", agent_id=WORKER, agent_type=f"{PLUGIN}:{STEP_ACTOR}")) is None


def test_h4_names_the_host_model_of_the_resolved_profile(rig: Rig) -> None:
    """Claude Code's dispatch accepts only model aliases: the RESULT names the alias
    `models.yaml` binds the resolved profile to."""
    rig.open_turn()
    rig.runner.script["resolve-step-model"] = build_report(
        "resolve-step-model", "resolved",
        profile={"slug": "claude-opus-4.8", "score": 9, "costRank": 8, "reason": "best"})
    request = main_thread("PreToolUse", tool_name="Bash", tool_use_id="m", tool_input={
        "command": f"python3 {HARNESS} resolve-step-model", "description": "model"})
    reason = _specific(rig.fire(request))["permissionDecisionReason"]
    assert "dispatch the resolved profile 'claude-opus-4.8' with the model 'opus'" in reason


def _return(tool_use_id: str, worker: str | None, status: str = "completed", **extra) -> dict:
    response = {"status": status, **({"agentId": worker} if worker else {})}
    return main_thread("PostToolUse", tool_name="Agent", tool_use_id=tool_use_id,
                       tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}", "prompt": "p",
                                   "run_in_background": False},
                       tool_response=response, **extra)


def test_h6_never_concludes_the_step_on_an_ambiguous_subagents_return(rig: Rig) -> None:
    """Review of #42: a subagent refused because its dispatcher is ambiguous has no
    ignored-return marker; its return must still leave the step in flight."""
    rig.open_turn()
    rig.open_step()
    nested = in_worker("PreToolUse", tool_name="Agent", tool_use_id="toolu_n",
                       tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}", "prompt": "p",
                                   "description": "d", "run_in_background": False})
    rig.fire(nested)
    rig.fire(dispatch("toolu_e"))
    rig.fire(event("SubagentStart", agent_id=OTHER_WORKER, agent_type=f"{PLUGIN}:{STEP_ACTOR}"))
    rig.fire(in_worker("SubagentStop", worker=OTHER_WORKER, stop_hook_active=False))
    assert rig.fire(_return("toolu_e", OTHER_WORKER)) is None
    assert "check-step-postconditions" not in rig.runner.functions()
    # The ambiguous start could not claim a dispatch, so its dispatch is still pending at its
    # return: the return is not evaluated either way.
    assert any("cannot be told apart" in line for line in rig.diagnostics)
    assert any("is not evaluated" in line or "stays in flight" in line for line in rig.diagnostics)
    rig.fire(in_worker("SubagentStop", stop_hook_active=False))
    rig.fire(_return("toolu_d", WORKER))
    assert rig.runner.functions().count("check-step-postconditions") == 1


def test_h6_never_concludes_the_step_on_a_non_framework_or_unknown_subagent(rig: Rig) -> None:
    rig.open_turn()
    rig.runner.script["start-session"] = [
        build_report("start-session", "not-applicable"),
    ]
    rig.open_step()
    assert rig.fire(_return("toolu_d", WORKER)) is None
    assert rig.fire(_return("toolu_x", "a0000000000000000")) is None
    assert "check-step-postconditions" not in rig.runner.functions()


def test_h6_never_concludes_the_step_on_a_background_return(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    decision = rig.fire(_return("toolu_d", WORKER, status="async_launched"))
    assert "runs in the background" in _specific(decision)["additionalContext"]
    assert "check-step-postconditions" not in rig.runner.functions()


def test_h6_without_a_returned_identity_falls_back_on_the_event_order(rig: Rig) -> None:
    rig.open_turn()
    rig.open_step()
    rig.fire(in_worker("SubagentStop", stop_hook_active=False))
    rig.fire(_return("toolu_d", None))
    assert rig.runner.functions().count("check-step-postconditions") == 1


def test_a_failed_dispatch_of_an_ungoverned_subagent_leaves_nothing_pending(rig: Rig) -> None:
    """Review of #42: the pending dispatch of an ungoverned agent is dropped at its return,
    so a later legitimate dispatch of the same actor is not refused as ambiguous."""
    rig.open_turn()
    ungoverned = "u0000000000000001"
    rig.fire(in_worker("PreToolUse", worker=ungoverned, tool_name="Agent", tool_use_id="toolu_u",
                       tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}", "prompt": "p",
                                   "run_in_background": False}))
    rig.fire(in_worker("PostToolUseFailure", worker=ungoverned, tool_name="Agent",
                       tool_use_id="toolu_u", tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}"},
                       error="interrupted"))
    rig.open_step()
    assert rig.tracker.read(WORKER).registered


def test_the_end_of_a_turn_drops_its_pending_dispatches(rig: Rig) -> None:
    rig.open_turn()
    rig.fire(dispatch("toolu_never_started"))
    rig.fire(main_thread("Stop", stop_hook_active=False))
    assert rig.tracker.claim_pending_dispatch(SESSION, f"{PLUGIN}:{STEP_ACTOR}") is None


def test_a_dispatch_that_failed_before_its_subagent_started_never_evaluates_the_step(rig: Rig) -> None:
    """Review of #42: a second dispatch failing before SubagentStart carries no agentId; it
    must not evaluate the step the first (still running) worker is bound to."""
    rig.open_turn()
    rig.open_step()
    rig.fire(dispatch("toolu_fails"))
    failed = main_thread("PostToolUseFailure", tool_name="Agent", tool_use_id="toolu_fails",
                         tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}"}, error="bad type")
    assert rig.fire(failed) is None
    assert "check-step-postconditions" not in rig.runner.functions()
    rig.fire(in_worker("SubagentStop", stop_hook_active=False))
    crashed = main_thread("PostToolUseFailure", tool_name="Agent", tool_use_id="toolu_d",
                          tool_input={"subagent_type": f"{PLUGIN}:{STEP_ACTOR}"}, error="crashed")
    rig.fire(crashed)
    assert rig.runner.functions().count("check-step-postconditions") == 1


def test_a_subagent_starting_after_a_plugin_update_is_refused_not_ungoverned(rig: Rig) -> None:
    """Review of #42: the dispatch was allowed, then the plugin changed before SubagentStart."""
    from dataclasses import replace

    rig.open_turn()
    rig.fire(dispatch())
    rig.tracker.write(SESSION, replace(rig.tracker.read(SESSION), harness_version="0.9.0"))
    rig.fire(event("SubagentStart", agent_id=WORKER, agent_type=f"{PLUGIN}:{STEP_ACTOR}"))
    record = rig.tracker.read(WORKER)
    assert record is not None and not record.registered
    assert "plugin was updated" in record.registration_failure
    write = in_worker("PreToolUse", tool_name="Write", tool_use_id="w",
                      tool_input={"file_path": "/home/user/acme-notes/workspace/a.md", "content": "y"})
    assert _denied(rig.fire(write))


def test_an_unreadable_pending_dispatch_refuses_the_started_subagent(rig: Rig, tmp_path: Path) -> None:
    """Review of #42: unreadable correlation state fails closed (refused), never ungoverned."""
    rig.open_turn()
    rig.fire(dispatch())
    (entry,) = (tmp_path / "state" / "dispatches" / SESSION).glob("*.json")
    entry.write_text("{not json", encoding="utf-8")
    rig.fire(event("SubagentStart", agent_id=WORKER, agent_type=f"{PLUGIN}:{STEP_ACTOR}"))
    record = rig.tracker.read(WORKER)
    assert record is not None and not record.registered
    assert "cannot be read" in record.registration_failure
