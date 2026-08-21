"""Fixtures for the `vscode-github-copilot-chat` adapter FUNCTIONAL suite.

The unit suite drives the adapter's classes IN PROCESS through a fake command runner. This
suite drives the real process boundary instead: host event JSON on stdin →
`adapters/dispatch.sh <event> vscode-github-copilot-chat [<agent>]` → `adapter.py` → a
harness command → host decision JSON on stdout, plus the exit code. Nothing in this
package imports the adapter; every assertion is made from outside the process.

Hermeticity is enforced by `stub_framework.py`: a tmp framework, a scripted stub harness,
and a child environment built from scratch. Contract validation reuses
`tests/unit/adapter/contract_assertions.py` rather than restating it — the same compiled
registry validates the stdin fixtures, the scripted reports, and the decisions that come
back.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_UNIT_ADAPTER_DIR = _REPO_ROOT / "tests" / "unit" / "adapter"

if str(_UNIT_ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(_UNIT_ADAPTER_DIR))

from harness_stub import HarnessStub
from hook_dispatch import HookRun, run_hook
from host_events import ORCHESTRATOR_AGENT, user_prompt_submit
from stub_framework import Framework, build_framework


@pytest.fixture
def framework(tmp_path: Path) -> Framework:
    """Build a disposable framework: copied adapters, stub harness, own environment."""
    return build_framework(
        root=tmp_path / "framework",
        script_path=tmp_path / "harness-script.json",
        journal_path=tmp_path / "harness-journal.jsonl",
    )


@pytest.fixture
def harness_stub(framework: Framework) -> HarnessStub:
    """Answer the scripted harness command API backing this framework."""
    return HarnessStub(
        script_path=Path(framework.environment["HARNESS_STUB_SCRIPT"]),
        journal_path=Path(framework.environment["HARNESS_STUB_JOURNAL"]),
    )


@pytest.fixture
def dispatch(framework: Framework) -> Callable[..., HookRun]:
    """Answer a callable firing one host event through this framework's `dispatch.sh`."""

    def _dispatch(
        event: str, payload: Mapping[str, Any], agent: str | None = None
    ) -> HookRun:
        return run_hook(
            dispatch_path=framework.dispatch_path,
            event=event,
            payload=payload,
            environment=framework.environment,
            agent=agent,
            cwd=framework.root,
        )

    return _dispatch


@pytest.fixture
def open_turn(dispatch: Callable[..., HookRun]) -> Callable[[], HookRun]:
    """Answer a callable opening the orchestrator turn session (H0) later hooks need."""

    def _open_turn() -> HookRun:
        return dispatch("UserPromptSubmit", user_prompt_submit(), ORCHESTRATOR_AGENT)

    return _open_turn
