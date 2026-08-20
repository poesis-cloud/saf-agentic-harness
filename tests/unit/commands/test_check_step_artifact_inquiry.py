"""Tests for `CheckStepArtifactInquiry` — function 9's input type."""

from __future__ import annotations

from pathlib import Path

from commands.check_step_artifact_inquiry import CheckStepArtifactInquiry
from commands.inquiry import Inquiry


class TestCheckStepArtifactInquiry:
    """Function 9's inquiry: the envelope plus the whole staged write set."""

    def test_carries_the_whole_write_set_as_one_immutable_unit(self) -> None:
        """Spec (contracts/api/check-step-artifact.input): `artifactPaths` is every path
        the just-landed write staged — the whole set of one tool call, validated and
        committed (or discarded) atomically as one unit; a single-path write is a set
        of one.
        """
        inquiry = CheckStepArtifactInquiry(
            session_id="s1",
            parent_session_id=None,
            artifact_paths=(Path("portfolio/epics/one.md"),),
        )

        assert isinstance(inquiry, Inquiry)
        assert inquiry.artifact_paths == (Path("portfolio/epics/one.md"),)
