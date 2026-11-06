"""Hook input reading — the captured Windows payloads are the ground truth."""

from __future__ import annotations

import pytest

from cli_rig import STDIN_CONTRACT, assert_matches_contract, captured_payloads
from copilot_cli_adapter.binding import ToolBinding
from copilot_cli_adapter.core import EVENTS, classify_calls
from copilot_cli_adapter.payload import HookInput, MalformedPayloadError, sanitize_identifier


def _hook(binding: ToolBinding, event: str, payload: dict) -> HookInput:
    return HookInput(event=event, payload=payload, binding=binding, scoping_agent=None)


def test_every_captured_payload_honors_the_stdin_contract_and_names_a_known_event() -> None:
    samples = captured_payloads()
    assert {sample["hookType"] for sample in samples} <= set(EVENTS)
    for sample in samples:
        assert_matches_contract(STDIN_CONTRACT, sample["input"])


def test_the_captured_batched_pre_tool_use_reads_every_call(binding: ToolBinding) -> None:
    batched = [
        s for s in captured_payloads()
        if s["hookType"] == "preToolUse" and len(s["input"]["toolCalls"]) > 1
    ]
    assert batched, "the capture holds a multi-call batch"
    calls = _hook(binding, "preToolUse", batched[0]["input"]).read_tool_calls()
    assert [call.name for call in calls] == ["powershell", "grep"]
    assert calls[0].args["command"] == "git status --short"
    assert calls[0].call_id == "toolu_014Ctab4aPLcbsDixpkxJ3FA"
    assert classify_calls(binding, calls) == ["H4", "-"]


def test_captured_tool_calls_classify_to_their_boundaries(binding: ToolBinding) -> None:
    seen = {}
    for sample in captured_payloads():
        if sample["hookType"] in ("preToolUse", "postToolUse", "postToolUseFailure"):
            calls = _hook(binding, sample["hookType"], sample["input"]).read_tool_calls()
            for call, boundary in zip(calls, classify_calls(binding, calls)):
                seen[call.name] = boundary
    assert seen == {
        "view": "-",
        "create": "H3",
        "edit": "H3",
        "powershell": "H4",
        "grep": "-",
        "task": "H2",
    }


def test_inside_a_subagent_the_host_id_is_the_agent_id(binding: ToolBinding) -> None:
    inner = next(
        s["input"] for s in captured_payloads()
        if s["hookType"] == "preToolUse" and s["input"]["sessionId"].startswith("ecdda9f8")
    )
    hook = _hook(binding, "preToolUse", inner)
    assert hook.host_id == "ecdda9f8-7300-43cc-9fae-ad08c0456604"
    started = next(s["input"] for s in captured_payloads() if s["hookType"] == "subagentStart")
    hook = _hook(binding, "subagentStart", started)
    assert hook.host_id == "1d793d07-adc6-4f25-bec3-5b03b44e949e"
    assert binding.probe(started, binding.host_worker_session_keys) == inner["sessionId"]


def test_the_documented_single_call_shape_is_read_too(binding: ToolBinding) -> None:
    hook = _hook(
        binding,
        "preToolUse",
        {"sessionId": "s", "toolName": "create", "toolArgs": '{"path": "C:\\\\a.md"}'},
    )
    (call,) = hook.read_tool_calls()
    assert call.name == "create" and call.args == {"path": "C:\\a.md"}


@pytest.mark.parametrize(
    "payload",
    [
        {"sessionId": "s"},
        {"sessionId": "s", "toolCalls": []},
        {"sessionId": "s", "toolCalls": "create"},
        {"sessionId": "s", "toolCalls": [{"args": {"path": "a"}}]},
        {"sessionId": "s", "toolCalls": [{"name": "", "args": {}}]},
        {"sessionId": "s", "toolCalls": ["create"]},
    ],
)
def test_an_unreadable_tool_call_shape_is_malformed(binding: ToolBinding, payload: dict) -> None:
    with pytest.raises(MalformedPayloadError):
        _hook(binding, "preToolUse", payload).read_tool_calls()


def test_identifiers_are_sanitized_for_file_names() -> None:
    assert sanitize_identifier("ab/../c d") not in ("ab/../c d",)
    assert "/" not in sanitize_identifier("a/b") and "\\" not in sanitize_identifier("a\\b")
