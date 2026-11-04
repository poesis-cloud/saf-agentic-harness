"""Shared rig for the `github-copilot-cli` adapter unit suite.

The adapter lives outside `src/` in a dashed directory, so that directory goes on
`sys.path` (as `adapter.py` does for itself). Its entry scripts share basenames with the
VS Code adapter's (`adapter.py`), so they are loaded by PATH under unique module names,
never imported by name.

Adapter spec I15: the adapter's only dependency is the command API, so every boundary test
drives it through a FAKE runner answering reports built — and contract-validated — by the
VS Code suite's own `build_report`.
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
ADAPTER_DIR = REPO_ROOT / "adapters" / "github-copilot-cli"
FIXTURES = REPO_ROOT / "tests" / "functional" / "adapter_copilot_cli" / "fixtures"
CAPTURED_PAYLOADS = FIXTURES / "copilot-app-windows-1.0.102.jsonl"

for _path in (
    ADAPTER_DIR,
    REPO_ROOT / "tests" / "unit" / "adapter",
    REPO_ROOT / "tests" / "functional" / "adapter",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from contract_assertions import assert_matches_contract  # noqa: E402
from copilot_cli_adapter.binding import ToolBinding, load_tool_binding  # noqa: E402
from copilot_cli_adapter.core import Adapter  # noqa: E402
from copilot_cli_adapter.invocation import HarnessInvocation  # noqa: E402
from copilot_cli_adapter.payload import HookInput  # noqa: E402
from copilot_cli_adapter.renderer import ContextRenderer  # noqa: E402
from copilot_cli_adapter.tracker import SessionTracker  # noqa: E402
from harness_stub import build_error_report, build_report, default_script  # noqa: E402

STDOUT_CONTRACT = "gsmarc://saf/adapters/github-copilot-cli/contracts/hook-stdout/v1"
STDIN_CONTRACT = "gsmarc://saf/adapters/github-copilot-cli/contracts/hook-stdin/v1"

ORCHESTRATOR = "release-train-engineer"
STEP_ACTOR = "system-architect"
HOST = "1d793d07-adc6-4f25-bec3-5b03b44e949e"
SUBAGENT = "ecdda9f8-7300-43cc-9fae-ad08c0456604"
TURN_TIMESTAMP = 1793784681555
TURN_SESSION = f"{HOST}-t{TURN_TIMESTAMP}"
CWD = r"C:\Users\cleme\saf-org"
WORKSPACE = r"C:\Users\cleme\saf-org\workspace"
PYTHON = r"C:\Users\cleme\saf-agentic-harness\.venv\Scripts\python.exe"
HARNESS = r"C:\Users\cleme\saf-agentic-harness\harness.py"
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


def assert_decision(decision: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Every non-empty decision the adapter emits honors its own stdout contract."""
    if decision:
        assert_matches_contract(STDOUT_CONTRACT, json.loads(json.dumps(decision)))
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


@dataclass
class Rig:
    adapter: Adapter
    runner: FakeRunner
    tracker: SessionTracker
    binding: ToolBinding
    diagnostics: list[str]

    def fire(
        self, event: str, payload: Mapping[str, Any], agent: str | None = None
    ) -> Mapping[str, Any] | None:
        hook = HookInput(
            event=event, payload=dict(payload), binding=self.binding, scoping_agent=agent
        )
        return assert_decision(self.adapter.handle(hook))

    def open_turn(self, host: str = HOST, timestamp: int = TURN_TIMESTAMP) -> None:
        self.fire(
            "userPromptSubmitted",
            {"sessionId": host, "prompt": "go", "timestamp": timestamp, "cwd": CWD},
            ORCHESTRATOR,
        )
        assert self.tracker.read(host) is not None

    def open_step(
        self, host: str = HOST, agent_id: str = SUBAGENT
    ) -> Mapping[str, Any] | None:
        return self.fire(
            "subagentStart",
            {
                "sessionId": host,
                "agentId": agent_id,
                "agentType": STEP_ACTOR,
                "agentName": STEP_ACTOR,
                "parentAgentId": host,
                "timestamp": 1793784701180,
                "cwd": CWD,
            },
        )


def make_invocation(**overrides: Any) -> HarnessInvocation:
    values: dict[str, Any] = {
        "python": PYTHON,
        "harness": HARNESS,
        "windows": True,
        "framework_dir": CWD,
        "workspace_dir": WORKSPACE,
        "environment": {"FRAMEWORK_DIR": CWD, "FRAMEWORK_WORKSPACE_DIR": WORKSPACE},
    }
    values.update(overrides)
    return HarnessInvocation(**values)


def pre_tool_use(*calls: tuple[str, Mapping[str, Any]], host: str = HOST) -> dict[str, Any]:
    """The OBSERVED preToolUse shape: batched, no timestamp."""
    return {
        "sessionId": host,
        "cwd": CWD,
        "toolCalls": [
            {"id": f"toolu_{index}", "name": name, "args": dict(args)}
            for index, (name, args) in enumerate(calls)
        ],
    }


def post_tool_use(
    name: str, args: Mapping[str, Any], host: str = HOST, event_failed: bool = False
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sessionId": host,
        "timestamp": 1793784686634,
        "cwd": CWD,
        "toolName": name,
        "toolArgs": dict(args),
    }
    if event_failed:
        payload["error"] = "Unhandled error"
    else:
        payload["toolResult"] = {"resultType": "success", "textResultForLlm": "ok"}
    return payload


@pytest.fixture(scope="session")
def binding() -> ToolBinding:
    return load_tool_binding(ADAPTER_DIR)


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
        invocation=make_invocation(layout_dir=str(tmp_path / "state" / "layouts")),
        diagnostic=diagnostics.append,
    )
    return Rig(adapter, runner, tracker, binding, diagnostics)


__all__ = [
    "ADAPTER_DIR",
    "CWD",
    "HARNESS",
    "HOST",
    "ORCHESTRATOR",
    "PYTHON",
    "STDIN_CONTRACT",
    "STEP_ACTOR",
    "SUBAGENT",
    "TURN_SESSION",
    "WORKSPACE",
    "FakeRunner",
    "Rig",
    "assert_decision",
    "assert_matches_contract",
    "binding",
    "build_error_report",
    "build_report",
    "captured_payloads",
    "load_script",
    "make_invocation",
    "post_tool_use",
    "pre_tool_use",
    "rig",
]
