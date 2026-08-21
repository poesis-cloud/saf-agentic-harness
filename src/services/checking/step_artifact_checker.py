"""Function 9 — `check-step-artifact`: the commit gate over one staged write set."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from errors import StateError
from services.checking.artifact_check import ArtifactCheck
from services.checking.artifact_check_report import ArtifactCheckReport
from services.checking.checking_service import CheckingService
from services.checking.revert import Revert
from stores.artifact_store import ArtifactStore
from stores.session_log_store import Log, Outcome, Report, SessionLogStore
from utils.clock import Clock

_FUNCTION = "check-step-artifact"
_VALID = "valid"
_REVERTED = "reverted"
_RESTORED = "restored"
_HEAD = "HEAD"


class StepArtifactChecker(CheckingService):
    """Validate the staged bytes, then commit the set or discard it whole.

    Spec (function 9): the write-boundary enforcement of C6 — a write lands in
    the working tree, this function validates it, and only a validated write is
    committed into workspace state (`HEAD`).
    """

    def __init__(
        self,
        session_log_store: SessionLogStore,
        artifact_store: ArtifactStore,
        clock: Clock | None = None,
    ) -> None:
        """Create the checker over its log store and the Git plane it transacts on."""
        super().__init__(session_log_store, clock)
        self._artifact_store = artifact_store

    def check_step_artifact(
        self,
        session_id: str,
        parent_session_id: str | None,
        artifact_paths: Sequence[Path],
    ) -> ArtifactCheckReport:
        """Validate the whole staged set of one tool call, atomically."""
        return cast(
            ArtifactCheckReport,
            self._execute_check(
                session_id, parent_session_id, artifact_paths=tuple(artifact_paths)
            ),
        )

    def _check_open_session(
        self,
        session_id: str,
        parent_session_id: str | None,
        log: Log,
        workflow_instance_id: str | None,
        **request: Any,
    ) -> Report:
        """Commit the validated set as ONE commit, or discard the whole set.

        Spec (function 9, Postconditions): one log entry per write validation,
        covering the whole set — when reverted, the same entry's report carries
        the revert records, so there is no second revert entry.
        """
        artifact_paths: tuple[Path, ...] = request["artifact_paths"]
        failures = self._validate_staged_set(artifact_paths)
        if failures:
            checks = self._discard_staged_set(artifact_paths, failures)
            outcome, reverted = Outcome(status=_REVERTED), checks
        else:
            self._artifact_store.commit_artifacts(artifact_paths, session_id=session_id)
            outcome, reverted = Outcome(status=_VALID), ()
        report = ArtifactCheckReport(
            context=self._build_context(
                _FUNCTION, session_id, parent_session_id, workflow_instance_id
            ),
            outcome=outcome,
            artifact_checks=reverted,
        )
        self._journal_report(session_id, report)
        return report

    def _build_report(
        self,
        session_id: str,
        parent_session_id: str | None,
        workflow_instance_id: str | None,
        outcome: Outcome,
    ) -> Report:
        """Build the envelope-only report of an error outcome."""
        return ArtifactCheckReport(
            context=self._build_context(
                _FUNCTION, session_id, parent_session_id, workflow_instance_id
            ),
            outcome=outcome,
        )

    def _validate_staged_set(
        self, artifact_paths: Sequence[Path]
    ) -> Mapping[str, str]:
        """Validate every staged path against its matched schema (invariant 1).

        Spec (function 9, precondition (E)): a path resolving to no artifact
        schema is abnormal — the whole staged set is discarded DEFENSIVELY before
        the `state-error` surfaces.
        """
        failures: dict[str, str] = {}
        try:
            for artifact_path in artifact_paths:
                findings = self._artifact_store.validate_artifact(artifact_path)
                if findings:
                    failures[artifact_path.as_posix()] = findings[0].message
        except StateError:
            self._revert_staged_set(artifact_paths)
            raise
        return failures

    def _discard_staged_set(
        self, artifact_paths: Sequence[Path], failures: Mapping[str, str]
    ) -> tuple[ArtifactCheck, ...]:
        """Discard every staged path of the call, recording the failing ones.

        Spec (function 9, invariant 2): call-level atomicity — any invalid path
        reverts the whole set, and only the failing paths are named.
        """
        reverts = self._revert_staged_set(artifact_paths)
        return tuple(
            ArtifactCheck(
                artifact_path=ref,
                failure_message=message,
                revert=Revert(
                    action=reverts[ref],
                    from_ref=_HEAD if reverts[ref] == _RESTORED else None,
                ),
            )
            for ref, message in failures.items()
        )

    def _revert_staged_set(self, artifact_paths: Sequence[Path]) -> Mapping[str, str]:
        """Restore every tracked path from `HEAD` and delete every newly created one."""
        return {
            artifact_path.as_posix(): self._artifact_store.revert_artifact(
                artifact_path
            ).rule
            for artifact_path in artifact_paths
        }


__all__ = ["StepArtifactChecker"]
