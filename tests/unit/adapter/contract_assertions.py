"""Contract assertions for the adapter suite — fixtures held to the real contracts.

Architect finding M3: every adapter assertion compared a hand-written dict against
another hand-written dict, so a fake report the real harness could never emit passed its
test and only failed later, at journaling time, against the contract it never met. These
helpers close that hole: every fake report the suite feeds the adapter is validated
against its function's OUTPUT contract, and every rendered decision against the adapter's
own seam-4 stdout contract, using the same `SchemaValidator` the harness itself runs on.

The compiled registry is built ONCE per test session — compiling every contract per
assertion dominates the suite's runtime.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from utils.schema_validator import SchemaValidator, ValidationErrorRecord

REPO_ROOT = Path(__file__).resolve().parents[3]

HOOK_STDIN_CONTRACT_ID = (
    "gsmarc://saf/adapters/vscode-github-copilot-chat/contracts/hook-stdin/v1"
)
HOOK_STDOUT_CONTRACT_ID = (
    "gsmarc://saf/adapters/vscode-github-copilot-chat/contracts/hook-stdout/v1"
)
_REPORT_CONTRACT_ID = "gsmarc://saf/contracts/api/{function}.output/v1"
_INQUIRY_CONTRACT_ID = "gsmarc://saf/contracts/api/{function}.input/v1"


@lru_cache(maxsize=1)
def contract_validator() -> SchemaValidator:
    """Compile every harness and adapter contract into one validator, once per session."""
    return SchemaValidator.compile_contracts(
        (
            *REPO_ROOT.glob("contracts/**/*.schema.json"),
            *REPO_ROOT.glob("adapters/*/contracts/*.schema.json"),
        )
    )


def _format(schema_id: str, records: tuple[ValidationErrorRecord, ...]) -> str:
    lines = "\n".join(f"  {record.path or '/'}: {record.message}" for record in records)
    return f"instance does not conform to {schema_id}:\n{lines}"


def assert_matches_contract(schema_id: str, instance: Any) -> Any:
    """Fail the calling test with every validation record unless `instance` conforms."""
    records = contract_validator().validate_instance(schema_id, instance)
    if records:
        raise AssertionError(_format(schema_id, records))
    return instance


def assert_report_matches_contract(
    function: str, report: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Assert a fake harness report is one the real function could actually return."""
    assert_matches_contract(_REPORT_CONTRACT_ID.format(function=function), dict(report))
    return report


def assert_inquiry_matches_contract(
    function: str, inquiry: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Assert an inquiry the adapter built conforms to the function's input contract."""
    assert_matches_contract(_INQUIRY_CONTRACT_ID.format(function=function), dict(inquiry))
    return inquiry


def assert_stdin_matches_contract(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Assert a stdin fixture is a payload the host could really have written."""
    assert_matches_contract(HOOK_STDIN_CONTRACT_ID, dict(payload))
    return payload


def assert_stdout_matches_contract(stdout: str) -> dict[str, Any]:
    """Assert a rendered decision's stdout conforms to the seam-4 contract; return it."""
    rendered = json.loads(stdout)
    assert_matches_contract(HOOK_STDOUT_CONTRACT_ID, rendered)
    return rendered


__all__ = [
    "HOOK_STDIN_CONTRACT_ID",
    "HOOK_STDOUT_CONTRACT_ID",
    "assert_inquiry_matches_contract",
    "assert_matches_contract",
    "assert_report_matches_contract",
    "assert_stdin_matches_contract",
    "assert_stdout_matches_contract",
    "contract_validator",
]
