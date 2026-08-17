import pytest
from jsonschema.exceptions import ValidationError


def assert_valid(validator, instance):
    validator.validate(instance)


def assert_invalid(validator, instance):
    with pytest.raises(ValidationError):
        validator.validate(instance)


class TestHookContracts:
    def test_hook_stdin_schema_is_draft_2020_12(self, make_validator, hook_stdin_schema):
        assert hook_stdin_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert hook_stdin_schema["$id"].startswith("gsmarc://")
        make_validator(hook_stdin_schema)

    def test_hook_stdout_schema_is_draft_2020_12(self, make_validator, hook_stdout_schema):
        assert hook_stdout_schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert hook_stdout_schema["$id"].startswith("gsmarc://")
        make_validator(hook_stdout_schema)

    def test_user_prompt_submit_stdin_examples_validate(self, make_validator, hook_stdin_schema):
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
        assert "Empty stdout on exit 0 is a valid passthrough" in hook_stdout_schema["description"]
