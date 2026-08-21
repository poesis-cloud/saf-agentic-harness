"""Unit tests for `ArtifactCheck` — function 9's per-path failure record."""

from __future__ import annotations

from services.checking import ArtifactCheck


class TestArtifactCheck:
    def test_renders_a_restored_paths_record(self) -> None:
        """Spec (function 9, worked example): a failing path's record carries its
        `artifactPath`, the failure message, and the revert action — `restored`
        naming `HEAD` as the source it came back from."""
        check = ArtifactCheck(
            artifact_path="portfolio/payments/features/feature-refunds.md",
            failure_message="frontmatter.status: 'shipped' is not one of the enum values",
            revert_action="restored",
            revert_from="HEAD",
        )

        assert check.to_dict() == {
            "artifactPath": "portfolio/payments/features/feature-refunds.md",
            "failureMessage": (
                "frontmatter.status: 'shipped' is not one of the enum values"
            ),
            "revert": {"action": "restored", "from": "HEAD"},
        }

    def test_renders_a_deleted_paths_record_without_a_source(self) -> None:
        """Spec (function 9, invariant 2): the discard DELETES newly created paths —
        a deletion restores from nothing, so the optional `from` is absent."""
        check = ArtifactCheck(
            artifact_path="review-report/refunds.json",
            failure_message="status: 'shipped' is not one of the enum values",
            revert_action="deleted",
        )

        assert check.to_dict()["revert"] == {"action": "deleted"}
