"""Fixtures for the `vscode-github-copilot-chat` adapter unit suite.

The adapter is NOT importable as a package (its directory name carries dashes and it
lives outside `src/`), so its own directory is put on `sys.path` here — the same way
`adapter.py` resolves its siblings when the host execs it as a script.

Adapter spec I15: the adapter's only dependency is the command API, so every test drives
it through a FAKE command runner returning contract-shaped report objects — never a real
harness service, store, or configuration object. "Contract-shaped" is enforced, not
asserted by eye: `build_report` validates against the function's own output contract.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

import pytest

from contract_assertions import (
    assert_inquiry_matches_contract,
    assert_report_matches_contract,
    assert_stdin_matches_contract,
    assert_stdout_matches_contract,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_ENV = "vscode-github-copilot-chat"
ADAPTER_DIR = REPO_ROOT / "adapters" / ADAPTER_ENV

if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def adapter_dir() -> Path:
    return ADAPTER_DIR


@pytest.fixture(scope="session")
def assert_valid_stdin():
    """Assert a stdin fixture is a payload the host could really have written."""
    return assert_stdin_matches_contract


@pytest.fixture(scope="session")
def assert_valid_stdout():
    """Assert a rendered decision's stdout validates against the seam-4 contract."""
    return assert_stdout_matches_contract


@pytest.fixture(scope="session")
def assert_valid_inquiry():
    """Assert an inquiry the adapter built validates against the function's contract."""
    return assert_inquiry_matches_contract


def build_report(
    function: str,
    status: str,
    session_id: str = "session-a",
    parent_session_id: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    """Build one report the real function could return — validated against its contract.

    Architect finding M3: a fake report is only a stand-in for the harness if the harness
    could have produced it, so construction itself is the checkpoint — a fixture the
    output contract rejects fails here rather than surviving to assert something false.
    """
    context: dict[str, Any] = {"function": function, "sessionId": session_id}
    if parent_session_id is not None:
        context["parentSessionId"] = parent_session_id
    report = {"context": context, "outcome": {"status": status}, **payload}
    assert_report_matches_contract(function, report)
    return report


def build_error_report(
    function: str,
    status: str = "state-error",
    code: str = "session-unregistered",
    message: str = "No session log exists.",
    session_id: str = "session-a",
) -> dict[str, Any]:
    """Build one ERROR report — the deny-by-default driver, held to the same contract."""
    report = {
        "context": {"function": function, "sessionId": session_id},
        "outcome": {
            "status": status,
            "error": {"code": code, "message": message, "retryable": False},
        },
    }
    assert_report_matches_contract(function, report)
    return report


@dataclass(frozen=True)
class RunnerCall:
    """One recorded invocation of the harness command API."""

    function: str
    inquiry: Mapping[str, Any]


@dataclass
class FakeCommandRunner:
    """A fake harness command API returning queued, contract-shaped reports.

    The queue per function is consumed one entry per call; the last entry is reused for
    any further call of that function, so a fan-out (H3) can be driven either by one
    report for all paths or by one report per path.
    """

    reports: MutableMapping[str, list[Mapping[str, Any]]] = field(default_factory=dict)
    failure: Exception | None = None
    calls: list[RunnerCall] = field(default_factory=list)

    def run_function(
        self, function: str, inquiry: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        self.calls.append(RunnerCall(function=function, inquiry=dict(inquiry)))
        if self.failure is not None:
            raise self.failure
        queue = self.reports.get(function)
        if not queue:
            status, payload = _DEFAULT_SUCCESS[function]
            return build_report(function, status, **payload)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def list_functions(self) -> list[str]:
        return [call.function for call in self.calls]

    def find_calls(self, function: str) -> list[RunnerCall]:
        return [call for call in self.calls if call.function == function]


_DEFAULT_AUTHORIZATION: Mapping[str, str] = {
    "actor": "product-manager",
    "artifactPath": "portfolio/a.md",
    "action": "update",
    "resource": "epic",
}

# Each default carries the result its success branch REQUIRES: a status alone is a report
# the real harness never returns, and the output contracts reject it (finding M3).
_DEFAULT_SUCCESS: Mapping[str, tuple[str, Mapping[str, Any]]] = {
    "start-session": (
        "started",
        {"session": {"sessionId": "session-a", "agent": "qa-engineer"}},
    ),
    "end-session": ("ended", {}),
    "resolve-workflow-instructions": ("resolved", {"instructions": ["reports-handling"]}),
    "resolve-workflow-skills": ("resolved", {"skills": ["code-review"]}),
    "resolve-step-instructions": ("resolved", {"instructions": ["reports-handling"]}),
    "resolve-step-skills": ("resolved", {"skills": ["code-review"]}),
    "check-step-preconditions": ("pass", {"conditionChecks": []}),
    "check-step-postconditions": ("pass", {"conditionChecks": []}),
    "check-step-authorization": ("allowed", {"authorization": _DEFAULT_AUTHORIZATION}),
    "check-step-artifact": ("valid", {}),
}


def queue_reports(**reports: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    """Build the fake runner's per-function report queues from snake_case kwargs."""
    return {
        function.replace("_", "-"): list(entries) for function, entries in reports.items()
    }
