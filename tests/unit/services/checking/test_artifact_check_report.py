"""Unit tests for `ArtifactCheckReport` — function 9's result type."""

from __future__ import annotations

from services.checking import ArtifactCheck, ArtifactCheckReport, Revert
from stores.session_log_store import Context, Error, Outcome
from write_boundary_fixtures import list_contract_violations

CONTEXT = Context(
    function="check-step-artifact",
    session_id="01j9xqr7t3",
    parent_session_id="01j9xq0f2m",
    workflow_instance_id="verification-01J9XQ",
)
FAILING_CHECK = ArtifactCheck(
    artifact_path="portfolio/payments/features/feature-refunds.md",
    failure_message="frontmatter.status: 'shipped' is not one of the enum values",
    revert=Revert(action="restored", from_ref="HEAD"),
)


class TestArtifactCheckReport:
    def test_renders_the_worked_examples_reverted_report(self) -> None:
        """Spec (function 9, worked example): the `reverted` report carries one
        `artifactChecks` record per FAILING path — its path, its failure message,
        and its revert action."""
        report = ArtifactCheckReport(
            context=CONTEXT,
            outcome=Outcome(status="reverted"),
            artifact_checks=(FAILING_CHECK,),
        )

        rendered = report.to_dict()

        assert rendered["outcome"] == {"status": "reverted"}
        assert rendered["artifactChecks"] == [FAILING_CHECK.to_dict()]
        assert list_contract_violations(report) == ()

    def test_omits_the_checks_property_on_the_valid_branch(self) -> None:
        """Spec (Classes): an empty `artifact_checks` tuple renders as the property's
        ABSENCE in the `valid` contract branch — `valid` asserts validity of the whole
        set, and a valid set has nothing to report per path."""
        report = ArtifactCheckReport(context=CONTEXT, outcome=Outcome(status="valid"))

        assert "artifactChecks" not in report.to_dict()
        assert list_contract_violations(report) == ()

    def test_omits_the_checks_property_on_an_error_outcome(self) -> None:
        """Spec (Outcomes rule 1): an error outcome carries the error detail and no
        function payload — the output contract's error branch never evaluates
        `artifactChecks`, and `unevaluatedProperties: false` forbids it."""
        report = ArtifactCheckReport(
            context=CONTEXT,
            outcome=Outcome(
                status="state-error",
                error=Error(
                    code="artifact-schema-unresolved",
                    message="No artifact schema resolves the path 'scratch/notes.json'.",
                    retryable=False,
                ),
            ),
        )

        assert "artifactChecks" not in report.to_dict()
        assert list_contract_violations(report) == ()
