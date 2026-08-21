"""Unit tests for `AuthorizationReport` — function 8's result type."""

from __future__ import annotations

from services.checking import Authorization, AuthorizationReport
from stores.session_log_store import Context, Error, Outcome
from write_boundary_fixtures import list_contract_violations

CONTEXT = Context(
    function="check-step-authorization",
    session_id="01j9xqr7t3",
    parent_session_id="01j9xq0f2m",
    workflow_instance_id="verification-01J9XQ",
)
AUTHORIZATION = Authorization(
    actor="qa-engineer",
    artifact_path="portfolio/epics/epic-payments.md",
    action="update",
    resource="epic",
)


class TestAuthorizationReport:
    def test_renders_the_worked_examples_denied_report(self) -> None:
        """Spec (function 8, worked example): the `denied` report carries the
        envelope plus the `authorization` object whose `failureMessage` names the
        missing privilege."""
        report = AuthorizationReport(
            context=CONTEXT,
            outcome=Outcome(status="denied"),
            authorization=Authorization(
                actor=AUTHORIZATION.actor,
                artifact_path=AUTHORIZATION.artifact_path,
                action=AUTHORIZATION.action,
                resource=AUTHORIZATION.resource,
                failure_message="missing privilege: update epic",
            ),
        )

        rendered = report.to_dict()

        assert rendered["outcome"] == {"status": "denied"}
        assert rendered["authorization"]["resource"] == "epic"
        assert list_contract_violations(report) == ()

    def test_renders_an_allowed_report_under_its_own_contract(self) -> None:
        """Spec (Report identity rule): the rendered report IS the contract object —
        the `allowed` branch requires `authorization` without a failure message."""
        report = AuthorizationReport(
            context=CONTEXT,
            outcome=Outcome(status="allowed"),
            authorization=AUTHORIZATION,
        )

        assert list_contract_violations(report) == ()

    def test_omits_the_authorization_property_on_an_error_outcome(self) -> None:
        """Spec (Outcomes rule 1): an error outcome carries the error detail and no
        function payload — the output contract's error branch never evaluates
        `authorization`, and `unevaluatedProperties: false` forbids it."""
        report = AuthorizationReport(
            context=CONTEXT,
            outcome=Outcome(
                status="inquiry-error",
                error=Error(
                    code="unknown-action",
                    message="Action 'read' is outside the contract vocabulary.",
                    retryable=False,
                ),
            ),
        )

        assert "authorization" not in report.to_dict()
        assert list_contract_violations(report) == ()
