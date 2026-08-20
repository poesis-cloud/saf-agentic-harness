"""Unit tests for `ConditionCheck` — the audit record of one condition outcome."""

from __future__ import annotations

import pytest

from config import StateCondition, StepCondition
from services.checking import ConditionCheck


class TestConditionCheck:
    def test_renders_a_step_condition_verbatim_under_its_slug(self) -> None:
        """Function 5, invariant 3: every check logs the FULL condition object with
        its outcome under that slug — condition slugs are the audit handle."""
        check = ConditionCheck(
            condition=StepCondition(
                kind="precondition", slug="after-build", step="build"
            ),
            outcome="pass",
        )

        assert check.to_dict() == {
            "condition": {
                "kind": "precondition",
                "slug": "after-build",
                "step": "build",
            },
            "outcome": "pass",
        }

    def test_renders_a_state_condition_verbatim_with_its_failure_message(self) -> None:
        """Function 5, Interface: the condition object itself, verbatim from the
        workflow configuration, its outcome, and its failureMessage when failing."""
        check = ConditionCheck(
            condition=StateCondition(
                kind="precondition",
                slug="report-exists",
                set_selector={
                    "setQuery": "artifacts['review-report'].filter(a, a.slug == artifact)"
                },
                set_predicate="selected.size() == 1",
            ),
            outcome="fail",
            failure_message="no artifact matches 'review-report'",
        )

        assert check.to_dict() == {
            "condition": {
                "kind": "precondition",
                "slug": "report-exists",
                "setSelector": {
                    "setQuery": "artifacts['review-report'].filter(a, a.slug == artifact)"
                },
                "setPredicate": "selected.size() == 1",
            },
            "outcome": "fail",
            "failureMessage": "no artifact matches 'review-report'",
        }

    def test_omits_the_failure_message_on_a_passing_check(self) -> None:
        """Output contract (`conditionChecks[]`): `failureMessage` is required when
        the outcome is `fail` and forbidden otherwise."""
        check = ConditionCheck(
            condition=StepCondition(kind="precondition", slug="after-build", step="build"),
            outcome="pass",
        )

        assert "failureMessage" not in check.to_dict()

    def test_is_frozen(self) -> None:
        """Python conventions: model classes are frozen dataclasses exposing public
        typed attributes directly."""
        check = ConditionCheck(
            condition=StepCondition(kind="precondition", slug="after-build", step="build"),
            outcome="pass",
        )

        with pytest.raises(Exception):
            check.outcome = "fail"  # type: ignore[misc]
