"""Tests for the adapter suite's own contract assertions — the M3 guard, guarded.

A validation helper that never fails is worse than none: it makes every fixture look
contract-checked while checking nothing. These tests hand it the exact defects that
reached `main` and require it to reject them.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from contract_assertions import (
    HOOK_STDOUT_CONTRACT_ID,
    assert_matches_contract,
    assert_report_matches_contract,
    assert_stdout_matches_contract,
    contract_validator,
)


def _precondition_report(condition: dict[str, Any]) -> dict[str, Any]:
    return {
        "context": {"function": "check-step-preconditions", "sessionId": "session-a"},
        "outcome": {"status": "fail"},
        "conditionChecks": [
            {
                "condition": condition,
                "outcome": "fail",
                "failureMessage": "no artifact matches 'review-report'",
            }
        ],
    }


_VALID_CONDITION: dict[str, Any] = {
    "kind": "precondition",
    "slug": "report-exists",
    "setSelector": {"setQuery": "artifacts['review-report']"},
    "setPredicate": "selected.size() > 0",
}


class TestContractAssertions:
    """The assertions must reject what the harness and the host would reject."""

    def test_accepts_a_report_the_real_function_could_return(self) -> None:
        """A helper that rejects valid fixtures is unusable — establish the baseline."""
        report = _precondition_report(_VALID_CONDITION)

        assert assert_report_matches_contract("check-step-preconditions", report) is report

    def test_rejects_the_underscore_condition_slug_that_reached_main(self) -> None:
        """`conditionSlug` is `^[a-z0-9-]+$` — the defect this guard exists to catch."""
        condition = {**_VALID_CONDITION, "slug": "report_exists"}

        with pytest.raises(AssertionError) as failure:
            assert_report_matches_contract(
                "check-step-preconditions", _precondition_report(condition)
            )

        assert "check-step-preconditions.output" in str(failure.value)

    def test_rejects_a_condition_that_is_neither_a_step_nor_a_state_condition(
        self,
    ) -> None:
        """A hyphenated slug alone is not conformance: `stepCondition` requires `step`
        and `stateCondition` requires `setSelector`/`setPredicate`, so a bare
        kind+slug object satisfies neither branch.
        """
        with pytest.raises(AssertionError):
            assert_report_matches_contract(
                "check-step-preconditions",
                _precondition_report({"kind": "precondition", "slug": "report-exists"}),
            )

    def test_rejects_a_status_only_report_whose_result_property_is_missing(self) -> None:
        """The `resolved` branch REQUIRES `instructions` — a status-only fake report is
        one the harness never returns.
        """
        with pytest.raises(AssertionError):
            assert_report_matches_contract(
                "resolve-workflow-instructions",
                {
                    "context": {
                        "function": "resolve-workflow-instructions",
                        "sessionId": "session-a",
                    },
                    "outcome": {"status": "resolved"},
                },
            )

    def test_rejects_a_report_carrying_a_foreign_function_discriminator(self) -> None:
        """Each output contract pins `context.function` — a report cannot be validated
        against a sibling function's contract by accident.
        """
        with pytest.raises(AssertionError):
            assert_report_matches_contract("end-session", _precondition_report(_VALID_CONDITION))

    def test_rejects_a_host_decision_matching_no_output_shape(self) -> None:
        """The seam-4 contract is a `oneOf` over the shapes the adapter may emit, and
        `permissionDecision` is `allow`/`deny` only — the host's third value, `ask`,
        matches no branch: the harness decides, it never defers to the user mid-boundary.
        """
        stdout = json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                }
            }
        )

        with pytest.raises(AssertionError):
            assert_stdout_matches_contract(stdout)

    def test_reports_every_violation_with_its_json_pointer(self) -> None:
        """The failure must name where the instance broke — a bare `False` would send the
        next reader back to hand-diffing dicts, which is the practice M3 found.
        """
        with pytest.raises(AssertionError) as failure:
            assert_matches_contract(
                HOOK_STDOUT_CONTRACT_ID,
                {"hookSpecificOutput": {"hookEventName": "NotAHostEvent"}},
            )

        message = str(failure.value)
        assert HOOK_STDOUT_CONTRACT_ID in message
        assert "hookSpecificOutput" in message

    def test_compiles_the_contract_registry_once_per_session(self) -> None:
        """Compiling every contract per assertion dominates the suite's runtime."""
        assert contract_validator() is contract_validator()
