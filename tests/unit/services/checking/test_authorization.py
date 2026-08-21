"""Unit tests for `Authorization` — function 8's decision detail."""

from __future__ import annotations

from services.checking import Authorization


class TestAuthorization:
    def test_renders_the_decision_detail_the_contract_requires(self) -> None:
        """Spec (contracts/api/check-step-authorization.output): the `authorization`
        object requires `actor`, `artifactPath`, `action`, and `resource` — the
        camelCase contract keys, never the Python attribute names."""
        authorization = Authorization(
            actor="qa-engineer",
            artifact_path="portfolio/epics/epic-payments.md",
            action="update",
            resource="epic",
        )

        assert authorization.to_dict() == {
            "actor": "qa-engineer",
            "artifactPath": "portfolio/epics/epic-payments.md",
            "action": "update",
            "resource": "epic",
        }

    def test_carries_no_failure_message_on_an_allow(self) -> None:
        """Spec (function 8, Interface): allow, OR deny with an
        `authorization.failureMessage` — the `allowed` contract branch explicitly
        forbids the property."""
        allowed = Authorization(
            actor="qa-engineer",
            artifact_path="review-report/refunds.json",
            action="create",
            resource="review-report",
        )

        assert "failureMessage" not in allowed.to_dict()

    def test_names_the_denial_cause_on_a_deny(self) -> None:
        """Spec (function 8, invariant 4): every deny's `failureMessage` names the
        cause — the missing privilege, the unsupported delete, the unresolvable
        resource, the logs path, or the unclean baseline."""
        denied = Authorization(
            actor="qa-engineer",
            artifact_path="portfolio/epics/epic-payments.md",
            action="update",
            resource="epic",
            failure_message="missing privilege: update epic",
        )

        assert denied.to_dict()["failureMessage"] == "missing privilege: update epic"
