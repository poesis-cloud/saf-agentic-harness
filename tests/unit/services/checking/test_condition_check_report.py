"""Unit tests for the condition-check report family shared by functions 5 and 10."""

from __future__ import annotations

import pytest

from config import StepCondition
from services.checking import (
    CheckStepPostconditionsReport,
    CheckStepPreconditionsReport,
    ConditionCheck,
)
from services.checking.condition_check_report import ConditionCheckReport
from stores.session_log_store import Context, Error, Outcome, Report


def _check(outcome: str = "pass") -> ConditionCheck:
    """Build one condition check for a report fixture."""
    return ConditionCheck(
        condition=StepCondition(kind="precondition", slug="after-build", step="build"),
        outcome=outcome,
        failure_message=None if outcome == "pass" else "predecessor not executed",
    )


class TestConditionCheckReport:
    def test_declares_no_contract_of_its_own(self) -> None:
        """Classes: the abstract base carries the shared payload — each function gets
        its own leaf type bound to its OWN output contract, so the base names none."""
        assert "CONTRACT_ID" not in vars(ConditionCheckReport)
        assert (
            CheckStepPreconditionsReport.CONTRACT_ID
            != CheckStepPostconditionsReport.CONTRACT_ID
        )

    def test_extends_the_report_envelope(self) -> None:
        """Report identity rule: `outcome` and `context` live on the `Report` base;
        each subtype adds its function-owned specific property."""
        assert issubclass(ConditionCheckReport, Report)

    def test_renders_the_condition_checks_payload_beside_the_envelope(self) -> None:
        """Function 5, Interface: `conditionChecks` — one check per declared
        condition, beside the aggregate `outcome`."""
        report = CheckStepPreconditionsReport(
            context=Context(
                function="check-step-preconditions",
                session_id="01j9xq0f2m",
                workflow_instance_id="verification-01J9XQ",
            ),
            outcome=Outcome(status="fail"),
            condition_checks=(_check("fail"),),
        )

        rendered = report.to_dict()

        assert rendered["context"]["function"] == "check-step-preconditions"
        assert rendered["outcome"] == {"status": "fail"}
        assert rendered["conditionChecks"][0]["outcome"] == "fail"

    def test_renders_an_empty_condition_checks_array_for_a_vacuous_pass(self) -> None:
        """Function 5, invariant 4: `outcome: pass` with an EMPTY `conditionChecks`
        array — an explicit entry, never a skipped invocation."""
        report = CheckStepPostconditionsReport(
            context=Context(function="check-step-postconditions", session_id="01j9xq0f2m"),
            outcome=Outcome(status="pass"),
        )

        assert report.to_dict()["conditionChecks"] == []

    def test_omits_condition_checks_on_a_not_applicable_outcome(self) -> None:
        """Output contract: the `not-applicable` branch declares no `conditionChecks`
        and the root forbids unevaluated properties; rule 2: it carries no
        function-specific payload."""
        report = CheckStepPreconditionsReport(
            context=Context(function="check-step-preconditions", session_id="01j9xq0f2m"),
            outcome=Outcome(status="not-applicable"),
        )

        assert "conditionChecks" not in report.to_dict()

    def test_omits_condition_checks_on_an_error_outcome(self) -> None:
        """Output contract: the error branch carries only the outcome and its error
        detail — statuses and structured fields are the normative test surface."""
        report = CheckStepPreconditionsReport(
            context=Context(function="check-step-preconditions", session_id="01j9xq0f2m"),
            outcome=Outcome(
                status="state-error",
                error=Error(
                    code="condition-evaluation-failed",
                    message="Condition 'report-exists' failed to evaluate.",
                    retryable=False,
                ),
            ),
        )

        rendered = report.to_dict()

        assert "conditionChecks" not in rendered
        assert rendered["outcome"]["error"]["code"] == "condition-evaluation-failed"


class TestCheckStepPreconditionsReport:
    def test_is_a_distinct_leaf_type(self) -> None:
        """Classes: functions 5 and 10 are structurally identical payloads bound to
        DISTINCT output contracts, so each is its own leaf type."""
        assert issubclass(CheckStepPreconditionsReport, ConditionCheckReport)
        assert not issubclass(CheckStepPreconditionsReport, CheckStepPostconditionsReport)


class TestCheckStepPostconditionsReport:
    def test_is_a_distinct_leaf_type(self) -> None:
        """Classes: which function produced a given report is read from
        `context.function`, never inferred from the type."""
        assert issubclass(CheckStepPostconditionsReport, ConditionCheckReport)
        assert not issubclass(CheckStepPostconditionsReport, CheckStepPreconditionsReport)
