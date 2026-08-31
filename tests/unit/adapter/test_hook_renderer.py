"""Unit tests for `HookRenderer` — the seam-4 host decision shapes."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import build_error_report, build_report
from event_class import EventClass
from hook_renderer import HookRenderer


@pytest.fixture
def framework_dirs(tmp_path: Path) -> tuple[Path, Path]:
    instructions_dir = tmp_path / "instructions"
    skills_dir = tmp_path / "skills"
    instructions_dir.mkdir()
    skills_dir.mkdir()
    (instructions_dir / "reports-handling.instructions.md").write_text(
        "Never surface step details to the user.\n", encoding="utf-8"
    )
    (instructions_dir / "assent.instructions.md").write_text(
        "Wait for explicit assent.\n", encoding="utf-8"
    )
    (skills_dir / "code-review.skill.md").write_text("# Code review\n", encoding="utf-8")
    return instructions_dir, skills_dir


@pytest.fixture
def renderer(framework_dirs: tuple[Path, Path]) -> HookRenderer:
    instructions_dir, skills_dir = framework_dirs
    return HookRenderer(instructions_dir=instructions_dir, skills_dir=skills_dir)


class TestHookRenderer:
    """Adapter spec — Hooks at a glance + each hook's Output construction."""

    def test_renders_pass_through_as_exit_zero_with_empty_stdout(
        self, renderer: HookRenderer
    ) -> None:
        """Adapter spec — Boundary binding (C7): a pass-through is exit 0, empty stdout,
        no journal entry.
        """
        decision = renderer.render_pass_through()

        assert decision.exit_code == 0
        assert decision.stdout == ""

    def test_renders_the_session_started_context_with_inlined_instructions(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H0, Output construction 1: each instruction ref is resolved
        against FRAMEWORK_INSTRUCTIONS_DIR and INLINED, in report order, each under a
        header naming its ref.
        """
        decision = renderer.render_context_injection(
            EventClass.SESSION_STARTED,
            (
                build_report(
                    "resolve-workflow-instructions",
                    "resolved",
                    instructions=["reports-handling", "assent"],
                ),
                build_report("resolve-workflow-skills", "resolved", skills=["code-review"]),
            ),
        )

        rendered = assert_valid_stdout(decision.stdout)
        context = rendered["hookSpecificOutput"]["additionalContext"]
        assert rendered["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "Never surface step details to the user." in context
        assert "Wait for explicit assent." in context
        assert context.index("reports-handling") < context.index("assent")

    def test_inlines_an_instruction_nested_under_an_actor_folder(
        self, framework_dirs: tuple[Path, Path], assert_valid_stdout
    ) -> None:
        """Instruction refs resolve by unique filename stem, including actor folders."""
        instructions_dir, skills_dir = framework_dirs
        nested = instructions_dir / "scrum-master"
        nested.mkdir()
        (instructions_dir / "assent.instructions.md").rename(
            nested / "assent.instructions.md"
        )
        renderer = HookRenderer(instructions_dir=instructions_dir, skills_dir=skills_dir)

        decision = renderer.render_context_injection(
            EventClass.SESSION_STARTED,
            (
                build_report(
                    "resolve-workflow-instructions",
                    "resolved",
                    instructions=["assent"],
                ),
            ),
        )

        rendered = assert_valid_stdout(decision.stdout)
        context = rendered["hookSpecificOutput"]["additionalContext"]
        assert "Wait for explicit assent." in context

    def test_renders_skills_as_load_directives_never_as_an_inline_dump(
        self, renderer: HookRenderer, framework_dirs: tuple[Path, Path], assert_valid_stdout
    ) -> None:
        """Adapter spec — Context-injection semantics (b) / H0 Output construction 2:
        skill injection must be a LOAD DIRECTIVE (resolved path + the instruction to read
        it), never an inline dump — inlining would defeat lazy loading.
        """
        _, skills_dir = framework_dirs

        decision = renderer.render_context_injection(
            EventClass.SESSION_STARTED,
            (
                build_report(
                    "resolve-workflow-instructions",
                    "resolved",
                    instructions=["reports-handling"],
                ),
                build_report("resolve-workflow-skills", "resolved", skills=["code-review"]),
            ),
        )

        context = assert_valid_stdout(decision.stdout)["hookSpecificOutput"][
            "additionalContext"
        ]
        assert str(skills_dir / "code-review.skill.md") in context
        assert "# Code review" not in context

    def test_renders_the_same_reports_byte_identically(
        self, renderer: HookRenderer
    ) -> None:
        """Adapter spec H1, invariant 2: rendering is a pure function of the two reports
        plus the framework layout — byte-identical re-render for identical reports.
        """
        reports = (
            build_report(
                "resolve-step-instructions", "resolved", instructions=["reports-handling"]
            ),
            build_report("resolve-step-skills", "resolved", skills=["code-review"]),
        )

        first = renderer.render_context_injection(EventClass.STEP_STARTED, reports)
        second = renderer.render_context_injection(EventClass.STEP_STARTED, reports)

        assert first.stdout == second.stdout

    def test_renders_the_step_started_context_under_its_own_event_name(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H1, Out: the step context carries `hookEventName: SubagentStart`
        — a mismatching name makes the host strip the whole output.
        """
        decision = renderer.render_context_injection(
            EventClass.STEP_STARTED,
            (
                build_report("resolve-step-instructions", "resolved", instructions=[]),
                build_report("resolve-step-skills", "resolved", skills=[]),
            ),
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["hookEventName"] == "SubagentStart"

    def test_renders_a_harness_error_at_an_inject_only_boundary_as_a_system_message(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H0, Output construction: on a harness error, exit 0 with a
        `systemMessage` naming the failure — NEVER exit 2 (this boundary must not veto
        the user's message).
        """
        decision = renderer.render_context_injection(
            EventClass.SESSION_STARTED,
            (
                build_error_report("resolve-workflow-instructions", "state-error"),
                build_report("resolve-workflow-skills", "resolved", skills=["code-review"]),
            ),
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert decision.exit_code == 0
        assert "session-unregistered" in rendered["systemMessage"]
        assert "hookSpecificOutput" not in rendered

    def test_renders_a_named_failure_as_a_system_message(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H0, Output construction: the adapter's own failure at an
        inject-only boundary surfaces the same way — exit 0, `systemMessage`, no veto.
        """
        decision = renderer.render_system_message("harness command failed: boom")

        rendered = assert_valid_stdout(decision.stdout)
        assert decision.exit_code == 0
        assert rendered["systemMessage"] == "harness command failed: boom"

    def test_renders_passing_preconditions_as_allow(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H2, Output construction: `permissionDecision` maps 1:1 from the
        report outcome — `pass -> allow`.
        """
        decision = renderer.render_permission_decision(
            (
                build_report(
                    "check-step-preconditions",
                    "pass",
                    conditionChecks=[
                        {
                            "condition": {
                                "kind": "precondition",
                                "slug": "after-build",
                                "step": "build",
                            },
                            "outcome": "pass",
                        }
                    ],
                ),
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"] == {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }

    def test_renders_failing_preconditions_as_deny_with_every_condition_check(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H2, Output construction: `fail -> deny`, the reason serializing
        every `conditionChecks[]` entry — slug, outcome, and `failureMessage` when failing
        — so the orchestrator receives exactly the report content.
        """
        decision = renderer.render_permission_decision(
            (
                build_report(
                    "check-step-preconditions",
                    "fail",
                    conditionChecks=[
                        {
                            "condition": {
                                "kind": "precondition",
                                "slug": "after-build",
                                "step": "build",
                            },
                            "outcome": "pass",
                        },
                        {
                            "condition": {
                                "kind": "precondition",
                                "slug": "report-exists",
                                "setSelector": {"setQuery": "artifacts['review-report']"},
                                "setPredicate": "selected.size() > 0",
                            },
                            "outcome": "fail",
                            "failureMessage": "no artifact matches 'review-report'",
                        },
                    ],
                ),
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        reason = rendered["hookSpecificOutput"]["permissionDecisionReason"]
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert reason == (
            "check-step-preconditions fail: [after-build] pass; "
            "[report-exists] fail — no artifact matches 'review-report'"
        )

    def test_renders_every_allowed_path_as_one_allow(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H3, Output construction: collapse over the per-path authorization
        reports — all allowed gives one `allow`.
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

        decision = renderer.render_permission_decision((allowed, allowed))

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_renders_any_denied_path_as_deny_naming_each_failure(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H3, Output construction: any denied path gives `deny`, the reason
        concatenating each denied path's `authorization.failureMessage`.
        """
        decision = renderer.render_permission_decision(
            (
                build_report(
                    "check-step-authorization",
                    "allowed",
                    authorization={
                        "actor": "product-manager",
                        "artifactPath": "portfolio/a.md",
                        "action": "update",
                        "resource": "epic",
                    },
                ),
                build_report(
                    "check-step-authorization",
                    "denied",
                    authorization={
                        "actor": "product-manager",
                        "artifactPath": "portfolio/epics/epic-payments.md",
                        "action": "update",
                        "resource": "epic",
                        "failureMessage": "missing privilege: update epic",
                    },
                ),
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert rendered["hookSpecificOutput"]["permissionDecisionReason"] == (
            "check-step-authorization denied: missing privilege: update epic "
            "(portfolio/epics/epic-payments.md)"
        )

    def test_renders_a_harness_error_at_a_gating_boundary_as_deny(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H2, invariant 1 (deny-by-default): any non-pass outcome —
        INCLUDING harness errors — denies; erring open would unmake the enforcement.
        """
        decision = renderer.render_permission_decision(
            (build_error_report("check-step-preconditions", "configuration-error", "unknown-workflow", "No such workflow."),)
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "unknown-workflow" in rendered["hookSpecificOutput"]["permissionDecisionReason"]

    def test_renders_an_adapter_side_denial_with_its_reason(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, Mechanics: the mediated-attribution denials are the adapter's
        own — they precede any harness invocation, so their reason is adapter-authored.
        """
        decision = renderer.render_denial("model-authored session attribution is never accepted")

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert rendered["hookSpecificOutput"]["permissionDecisionReason"] == (
            "model-authored session attribution is never accepted"
        )

    def test_renders_the_stamped_command_as_a_full_updated_input(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H4, rule 4: rewrite via `updatedInput`, injecting
        `--session-id <resolved current session>` — the object must carry the FULL
        rewritten `tool_input`, never a patch.
        """
        decision = renderer.render_stamped_input(
            tool_input={
                "command": "harness.py resolve-step --workflow verification",
                "explanation": "resolve the next step",
            },
            command_key="command",
            session_id="chat-session-guid-t2026-07-11t14-32-07-000z",
        )

        rendered = assert_valid_stdout(decision.stdout)
        hook_output = rendered["hookSpecificOutput"]
        assert hook_output["permissionDecision"] == "allow"
        assert hook_output["updatedInput"] == {
            "command": (
                "harness.py resolve-step --workflow verification "
                "--session-id chat-session-guid-t2026-07-11t14-32-07-000z"
            ),
            "explanation": "resolve the next step",
        }

    def test_renders_a_valid_write_as_a_plain_continue(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H5, Output construction: `outcome: valid` renders plain success —
        the commit already happened harness-side; nothing to tell the model.
        """
        decision = renderer.render_write_outcome(build_report("check-step-artifact", "valid"))

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered == {"continue": True}

    def test_renders_a_reverted_write_as_a_block_with_the_revert_record(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H5, Output construction: `outcome: reverted` renders
        `decision: block` with the `artifactChecks[]` failure messages verbatim plus the
        retry directive as `additionalContext`.
        """
        decision = renderer.render_write_outcome(
            build_report(
                "check-step-artifact",
                "reverted",
                artifactChecks=[
                    {
                        "artifactPath": "portfolio/payments/features/feature-refunds.md",
                        "failureMessage": "frontmatter.status: 'shipped' is not one of the enum values",
                        "revert": {"action": "restored"},
                    }
                ],
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["decision"] == "block"
        assert rendered["reason"] == (
            "check-step-artifact reverted portfolio/payments/features/feature-refunds.md: "
            "frontmatter.status: 'shipped' is not one of the enum values (restored)"
        )
        assert rendered["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert "Rewrite the artifact" in rendered["hookSpecificOutput"]["additionalContext"]

    def test_renders_a_harness_error_at_the_commit_gate_as_a_block(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H5, Output construction: harness errors render
        `decision: block` + error detail — a failed commit never reads as valid.
        """
        decision = renderer.render_write_outcome(
            build_error_report("check-step-artifact", "system-error", "commit-failed", "git refused")
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["decision"] == "block"
        assert "commit-failed" in rendered["reason"]

    def test_renders_passing_postconditions_as_a_plain_continue(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H6, Output construction: `pass` renders plain success — the
        orchestrator proceeds to the next `resolve-step` per its instructions.
        """
        decision = renderer.render_step_outcome(
            build_report("check-step-postconditions", "pass", conditionChecks=[])
        )

        assert assert_valid_stdout(decision.stdout) == {"continue": True}

    def test_renders_failing_postconditions_as_a_block_restating_reports_handling(
        self, renderer: HookRenderer, assert_valid_stdout
    ) -> None:
        """Adapter spec H6, Output construction: `fail` renders `decision: block` with the
        serialized `conditionChecks[]`, and `additionalContext` restating the injected
        `reports-handling` reaction so the failure stays inside the workflow.
        """
        decision = renderer.render_step_outcome(
            build_report(
                "check-step-postconditions",
                "fail",
                conditionChecks=[
                    {
                        "condition": {
                            "kind": "postcondition",
                            "slug": "report-exists",
                            "setSelector": {"setQuery": "artifacts['review-report']"},
                            "setPredicate": "selected.size() > 0",
                        },
                        "outcome": "fail",
                        "failureMessage": "no artifact matches 'review-report'",
                    }
                ],
            )
        )

        rendered = assert_valid_stdout(decision.stdout)
        assert rendered["decision"] == "block"
        assert rendered["reason"] == (
            "check-step-postconditions fail: [report-exists] fail — "
            "no artifact matches 'review-report'"
        )
        assert "re-resolve" in rendered["hookSpecificOutput"]["additionalContext"]
        assert "do not surface step details to the user" in (
            rendered["hookSpecificOutput"]["additionalContext"]
        )
