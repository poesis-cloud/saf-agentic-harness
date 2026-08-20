"""Fixtures for the `vscode-github-copilot-chat` adapter unit suite.

The adapter is NOT importable as a package (its directory name carries dashes and it
lives outside `src/`), so its own directory is put on `sys.path` here — the same way
`adapter.py` resolves its siblings when the host execs it as a script.

Adapter spec I15: the adapter's only dependency is the command API, so every test drives
it through a FAKE command runner returning contract-shaped report objects — never a real
harness service, store, or configuration object.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

import pytest
from jsonschema import validators
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO_ROOT = Path(__file__).resolve().parents[3]
ADAPTER_ENV = "vscode-github-copilot-chat"
ADAPTER_DIR = REPO_ROOT / "adapters" / ADAPTER_ENV

if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def adapter_dir() -> Path:
    return ADAPTER_DIR


@pytest.fixture(scope="session")
def hook_stdin_schema() -> dict[str, Any]:
    return _load_json(ADAPTER_DIR / "contracts" / "hook-stdin.schema.json")


@pytest.fixture(scope="session")
def hook_stdout_schema() -> dict[str, Any]:
    return _load_json(ADAPTER_DIR / "contracts" / "hook-stdout.schema.json")


def _resource_from_schema(schema: Mapping[str, Any]) -> Resource:
    try:
        return Resource.from_contents(schema, default_specification=DRAFT202012)
    except TypeError:
        return Resource.from_contents(schema)


@pytest.fixture(scope="session")
def contract_registry() -> Registry:
    registry = Registry()
    schema_paths = [
        *REPO_ROOT.glob("contracts/**/*.schema.json"),
        *REPO_ROOT.glob("adapters/*/contracts/*.schema.json"),
    ]
    for schema_path in schema_paths:
        schema = _load_json(schema_path)
        schema_id = schema.get("$id")
        if schema_id:
            registry = registry.with_resource(schema_id, _resource_from_schema(schema))
    return registry


@pytest.fixture(scope="session")
def make_validator(contract_registry: Registry):
    def _make_validator(schema: Mapping[str, Any]):
        validator_cls = validators.validator_for(schema)
        validator_cls.check_schema(schema)
        return validator_cls(schema, registry=contract_registry)

    return _make_validator


@pytest.fixture
def assert_valid_stdin(make_validator, hook_stdin_schema: dict[str, Any]):
    """Assert a stdin fixture is a payload the host could really have written."""
    validator = make_validator(hook_stdin_schema)

    def _assert(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        validator.validate(dict(payload))
        return payload

    return _assert


@pytest.fixture
def assert_valid_stdout(make_validator, hook_stdout_schema: dict[str, Any]):
    """Assert a rendered decision's stdout validates against the seam-4 contract."""
    validator = make_validator(hook_stdout_schema)

    def _assert(stdout: str) -> dict[str, Any]:
        rendered = json.loads(stdout)
        validator.validate(rendered)
        return rendered

    return _assert


@pytest.fixture
def assert_valid_inquiry(make_validator, repo_root: Path):
    """Assert an inquiry the adapter built validates against the function's contract."""

    def _assert(function: str, inquiry: Mapping[str, Any]) -> Mapping[str, Any]:
        schema = _load_json(
            repo_root / "contracts" / "api" / f"{function}.input.schema.json"
        )
        make_validator(schema).validate(dict(inquiry))
        return inquiry

    return _assert


def build_report(
    function: str,
    status: str,
    session_id: str = "session-a",
    parent_session_id: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    """Build one contract-shaped report object — the harness command API's `out`."""
    context: dict[str, Any] = {"function": function, "sessionId": session_id}
    if parent_session_id is not None:
        context["parentSessionId"] = parent_session_id
    return {"context": context, "outcome": {"status": status}, **payload}


def build_error_report(
    function: str,
    status: str = "state-error",
    code: str = "session-unregistered",
    message: str = "No session log exists.",
    session_id: str = "session-a",
) -> dict[str, Any]:
    """Build one contract-shaped ERROR report — the deny-by-default driver."""
    return {
        "context": {"function": function, "sessionId": session_id},
        "outcome": {
            "status": status,
            "error": {"code": code, "message": message, "retryable": False},
        },
    }


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
            return build_report(function, _DEFAULT_SUCCESS_STATUS[function])
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def list_functions(self) -> list[str]:
        return [call.function for call in self.calls]

    def find_calls(self, function: str) -> list[RunnerCall]:
        return [call for call in self.calls if call.function == function]


_DEFAULT_SUCCESS_STATUS: Mapping[str, str] = {
    "start-session": "started",
    "end-session": "ended",
    "resolve-workflow-instructions": "resolved",
    "resolve-workflow-skills": "resolved",
    "resolve-step-instructions": "resolved",
    "resolve-step-skills": "resolved",
    "check-step-preconditions": "pass",
    "check-step-postconditions": "pass",
    "check-step-authorization": "allowed",
    "check-step-artifact": "valid",
}


def queue_reports(**reports: Sequence[Mapping[str, Any]]) -> dict[str, list[Any]]:
    """Build the fake runner's per-function report queues from snake_case kwargs."""
    return {
        function.replace("_", "-"): list(entries) for function, entries in reports.items()
    }
