"""Shared rig for the `claude-code` adapter unit suite.

Adapter spec I15: the adapter's only dependency is the command API, so every boundary test
drives it through a FAKE runner answering reports built — and contract-validated — by the
shared harness stub. Payloads take the shapes captured on Claude Code 2.1.323
(`tests/functional/adapter_claude_code/fixtures/`).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_DIR = REPO_ROOT / "adapters" / "claude-code"
CAPTURED_PAYLOADS = (
    REPO_ROOT / "tests" / "functional" / "adapter_claude_code" / "fixtures"
    / "claude-code-linux-2.1.323.jsonl"
)

if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from claude_code_adapter import renderer  # noqa: E402
from claude_code_adapter.core import Adapter  # noqa: E402
from contract_assertions import assert_matches_contract  # noqa: E402
from harness_stub import build_error_report, build_report, default_script  # noqa: E402
from saf_host_adapter.binding import ToolBinding, load_tool_binding  # noqa: E402
from saf_host_adapter.invocation import HarnessInvocation  # noqa: E402
from saf_host_adapter.payload import HookInput  # noqa: E402
from saf_host_adapter.reports import ContextRenderer  # noqa: E402
from saf_host_adapter.tracker import SessionTracker  # noqa: E402

STDOUT_CONTRACT = "gsmarc://saf/adapters/claude-code/contracts/hook-stdout/v1"
STDIN_CONTRACT = "gsmarc://saf/adapters/claude-code/contracts/hook-stdin/v1"

PLUGIN = "poesis-saf-agentic-organization"
ORCHESTRATOR = "release-train-engineer"
STEP_ACTOR = "system-architect"
SESSION = "cc5264f0-0ee1-4fcf-ab92-310faa224d3c"
PROMPT = "b5851f31-6ac0-470e-979b-b79a2a2b1871"
TURN_SESSION = f"{SESSION}-t{PROMPT}"
WORKER = "abaaf063a6f522403"
OTHER_WORKER = "a8a46e7597a677259"
CWD = "/home/user/acme-notes"
WORKSPACE = "/home/user/acme-notes/workspace"
PYTHON = "/home/user/saf-agentic-harness/.venv/bin/python"
HARNESS = "/home/user/saf-agentic-harness/harness.py"
INSTRUCTION_REF = "reports-handling"
SKILL_ID = "code-review"


def load_script(name: str, filename: str) -> ModuleType:
    """Load one of the adapter's entry scripts under a module name of its own."""
    spec = importlib.util.spec_from_file_location(name, ADAPTER_DIR / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def captured_payloads() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in CAPTURED_PAYLOADS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def assert_decision(decision: Mapping[str, Any] | None, event: str) -> Mapping[str, Any] | None:
    """Every non-empty decision the adapter emits, rendered for its event, honors the
    adapter's own stdout contract."""
    if decision:
        assert_matches_contract(STDOUT_CONTRACT, json.loads(renderer.render(decision, event)))
    return decision


def _default_reports() -> dict[str, Any]:
    return {function: answer["report"] for function, answer in default_script().items()}


@dataclass
class FakeRunner:
    """Answer scripted reports; journal every (function, inquiry)."""

    script: dict[str, Any] = field(default_factory=_default_reports)
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def run_function(self, function: str, inquiry: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((function, dict(inquiry)))
        answer = self.script.get(function)
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise RuntimeError(f"no scripted answer for {function}")
        return answer

    def functions(self) -> list[str]:
        return [function for function, _ in self.calls]

    def inquiries(self, function: str) -> list[dict[str, Any]]:
        return [inquiry for name, inquiry in self.calls if name == function]


def event(name: str, **fields: Any) -> dict[str, Any]:
    """A Claude Code payload: every event carries the conversation and its folder."""
    return {"session_id": SESSION, "hook_event_name": name, "cwd": CWD, **fields}


def main_thread(name: str, **fields: Any) -> dict[str, Any]:
    return event(name, agent_type=f"{PLUGIN}:{ORCHESTRATOR}", **fields)


def in_worker(name: str, worker: str = WORKER, **fields: Any) -> dict[str, Any]:
    return event(name, agent_id=worker, agent_type=f"{PLUGIN}:{STEP_ACTOR}", **fields)


def dispatch(tool_use_id: str = "toolu_d", **overrides: Any) -> dict[str, Any]:
    tool_input = {
        "subagent_type": f"{PLUGIN}:{STEP_ACTOR}",
        "description": "d",
        "prompt": "p",
        "run_in_background": False,
        **overrides,
    }
    return main_thread("PreToolUse", tool_name="Agent", tool_use_id=tool_use_id,
                       tool_input={k: v for k, v in tool_input.items() if v is not None})


@dataclass
class Rig:
    adapter: Adapter
    runner: FakeRunner
    tracker: SessionTracker
    binding: ToolBinding
    diagnostics: list[str]

    def fire(self, payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        name = str(payload["hook_event_name"])
        assert_matches_contract(STDIN_CONTRACT, dict(payload))
        hook = HookInput(
            event=name, payload=dict(payload), binding=self.binding, dispatcher_from_session=False
        )
        return assert_decision(self.adapter.handle(hook), name)

    def open_turn(self) -> Mapping[str, Any] | None:
        decision = self.fire(main_thread("UserPromptSubmit", prompt_id=PROMPT, prompt="go"))
        assert self.tracker.read(SESSION) is not None
        return decision

    def open_step(self, worker: str = WORKER, tool_use_id: str = "toolu_d") -> Mapping[str, Any] | None:
        assert self.fire(dispatch(tool_use_id)) is None
        return self.fire(event("SubagentStart", agent_id=worker,
                               agent_type=f"{PLUGIN}:{STEP_ACTOR}"))


@pytest.fixture(scope="session")
def binding() -> ToolBinding:
    return load_tool_binding(ADAPTER_DIR, gating_probes=load_script("claude_code_entry", "adapter.py").GATING_PROBES)


@pytest.fixture
def rig(tmp_path: Path, binding: ToolBinding) -> Rig:
    instructions = tmp_path / "instructions"
    instructions.mkdir()
    (instructions / f"{INSTRUCTION_REF}.instructions.md").write_text(
        "Handle reports carefully.\n", encoding="utf-8"
    )
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / f"{SKILL_ID}.skill.md").write_text("# Code review\n", encoding="utf-8")
    runner = FakeRunner()
    tracker = SessionTracker(tmp_path / "state")
    diagnostics: list[str] = []
    adapter = Adapter(
        binding=binding,
        tracker=tracker,
        context_renderer=ContextRenderer(instructions, skills),
        runner=runner,
        workspace_dir=WORKSPACE,
        invocation=HarnessInvocation(
            python=PYTHON, harness=HARNESS, windows=False, framework_dir=CWD,
            workspace_dir=WORKSPACE,
        ),
        diagnostic=diagnostics.append,
        make_folders=lambda root, parts: None,
        harness_version="1.0.0a3",
    )
    return Rig(adapter, runner, tracker, binding, diagnostics)


__all__ = [
    "build_error_report",
    "build_report",
]
