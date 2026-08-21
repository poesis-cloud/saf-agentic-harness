"""Unit tests for `ArtifactCheck` — function 9's per-path failure record."""

from __future__ import annotations

from services.checking import ArtifactCheck, Revert


class TestArtifactCheck:
    def test_renders_a_restored_paths_record(self) -> None:
        """Spec (function 9, worked example): a failing path's record carries its
        `artifactPath`, the failure message, and the revert action — `restored`
        naming `HEAD` as the source it came back from."""
        check = ArtifactCheck(
            artifact_path="portfolio/payments/features/feature-refunds.md",
            failure_message="frontmatter.status: 'shipped' is not one of the enum values",
            revert=Revert(action="restored", from_ref="HEAD"),
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
            revert=Revert(action="deleted"),
        )

        assert check.to_dict()["revert"] == {"action": "deleted"}

    def test_carries_the_revert_as_a_typed_record_not_two_flattened_fields(self) -> None:
        """Spec (Classes, report identity rule): every service returns typed results,
        never bare dicts — the class diagram gives this record a nested
        `Revert(action, from_ref)`, so the nesting is modelled, not rebuilt at render
        time out of two flat attributes."""
        revert = Revert(action="restored", from_ref="HEAD")
        check = ArtifactCheck(
            artifact_path="review-report/refunds.json",
            failure_message="status: 'shipped' is not one of the enum values",
            revert=revert,
        )

        assert check.revert is revert
        assert not hasattr(check, "revert_action")
        assert not hasattr(check, "revert_from")


class TestRevert:
    def test_exposes_frozen_revert_fields(self) -> None:
        """Spec (Classes): "Frozen dataclasses throughout: public typed attributes, no
        getters/setters"."""
        revert = Revert(action="restored", from_ref="HEAD")

        assert (revert.action, revert.from_ref) == ("restored", "HEAD")
        assert Revert(action="deleted").from_ref is None
