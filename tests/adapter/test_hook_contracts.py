import pytest
from jsonschema.exceptions import ValidationError


def assert_valid(validator, instance):
    validator.validate(instance)


def assert_invalid(validator, instance):
    with pytest.raises(ValidationError):
        validator.validate(instance)


class TestHookContracts:
    def test_hook_stdin_schema_is_draft_2020_12(self, make_validator, hook_stdin_schema):
        """Adapter spec (Invocation plumbing, seam 1): the host event payload has an
        adapter-owned contract, published under a `gsmarc://` id like every other contract
        in the repository."""
        assert hook_stdin_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert hook_stdin_schema["$id"].startswith("gsmarc://")
        make_validator(hook_stdin_schema)

    def test_hook_stdout_schema_is_draft_2020_12(self, make_validator, hook_stdout_schema):
        """Adapter spec (Invocation plumbing, seam 4): the decision the adapter writes back to
        the host has an adapter-owned contract, published under a `gsmarc://` id."""
        assert hook_stdout_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert hook_stdout_schema["$id"].startswith("gsmarc://")
        make_validator(hook_stdout_schema)

    def test_user_prompt_submit_stdin_examples_validate(self, make_validator, hook_stdin_schema):
        """Adapter spec H0, In: the `UserPromptSubmit` payload H0 opens a session from — its
        `cwd` and `prompt` are required, so an event missing them is rejected at the stdin
        seam rather than half-handled inside the hook."""
        validator = make_validator(hook_stdin_schema)

        assert_valid(
            validator,
            {
                "timestamp": "2026-07-11T14:32:07.000Z",
                "hook_event_name": "UserPromptSubmit",
                "session_id": "chat-session-guid",
                "transcript_path": "/home/user/transcript.jsonl",
                "cwd": "/abs/framework/root",
                "prompt": "run the workflow",
            },
        )
        assert_invalid(
            validator,
            {
                "timestamp": "2026-07-11T14:32:07.000Z",
                "hook_event_name": "UserPromptSubmit",
                "session_id": "chat-session-guid",
            },
        )

    def test_pre_tool_use_stdin_examples_validate(self, make_validator, hook_stdin_schema):
        """Adapter spec H2–H4, In: the `PreToolUse` payload the dispatch, write and mediated
        classes are classified from — `tool_name`, `tool_input` and the `cwd` the adapter
        resolves the framework against."""
        validator = make_validator(hook_stdin_schema)

        assert_valid(
            validator,
            {
                "timestamp": "2026-07-11T14:32:08.000Z",
                "hook_event_name": "PreToolUse",
                "session_id": "chat-session-guid",
                "cwd": "/abs/framework/root",
                "tool_name": "runSubagent",
                "tool_input": {
                    "agentName": "qa-engineer",
                    "model": "Claude Sonnet 4.6 (copilot)",
                    "prompt": "execute this step",
                },
                "tool_use_id": "call_abc123",
            },
        )
        assert_invalid(
            validator,
            {
                "timestamp": "2026-07-11T14:32:08.000Z",
                "hook_event_name": "PreToolUse",
                "session_id": "chat-session-guid",
                "tool_name": "runSubagent",
                "tool_input": {"agentName": "qa-engineer"},
            },
        )

    def test_unknown_or_mismatched_stdin_event_fails(self, make_validator, hook_stdin_schema):
        """Adapter spec (Hook registration): an event the adapter never registers — here
        `SessionStart` — is not admitted by the stdin contract either; the firing surface is
        exactly H0–H7 at both the registration and the payload seam."""
        validator = make_validator(hook_stdin_schema)

        assert_invalid(
            validator,
            {
                "timestamp": "2026-07-11T14:32:08.000Z",
                "hook_event_name": "SessionStart",
                "session_id": "chat-session-guid",
            },
        )

    def test_stdout_examples_validate(self, make_validator, hook_stdout_schema):
        """Adapter spec H0/H3/H6, Out: the four decision shapes the adapter may render —
        `additionalContext`, a `PreToolUse` deny with its reason, a bare continue, and a
        `decision: block` carrying both a reason and follow-up context."""
        validator = make_validator(hook_stdout_schema)

        assert_valid(
            validator,
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "rendered workflow context",
                }
            },
        )
        assert_valid(
            validator,
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "check-step-preconditions fail: [report_exists] fail",
                }
            },
        )
        assert_valid(validator, {"continue": True})
        assert_valid(
            validator,
            {
                "decision": "block",
                "reason": "check-step-postconditions fail: [report_exists] fail",
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": "Per reports-handling: re-resolve.",
                },
            },
        )

    def test_stdout_invalid_adapter_decisions_fail(self, make_validator, hook_stdout_schema):
        """Adapter spec H3, invariant 3 (deny-by-default): the decision vocabulary is closed —
        `ask` is not one of the adapter's decisions, a block without its reason is rejected,
        and context must be text. The adapter can render no third, ambiguous answer."""
        validator = make_validator(hook_stdout_schema)

        assert_invalid(
            validator,
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                }
            },
        )
        assert_invalid(validator, {"decision": "block"})
        assert_invalid(
            validator,
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": 42,
                }
            },
        )

    def test_empty_stdout_passthrough_is_intentionally_out_of_schema_scope(
        self, hook_stdout_schema
    ):
        """Adapter spec (Boundary binding, C7): pass-through is empty stdout on exit 0 — the
        absence of a decision, not a decision. The contract records that exclusion instead of
        modelling an empty object, so pass-through can never be confused with a rendered
        answer."""
        assert "Empty stdout on exit 0 is a valid passthrough" in hook_stdout_schema["description"]
