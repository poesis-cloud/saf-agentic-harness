"""Function 8 — `check-step-authorization`: is this write a granted privilege?"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from config import AccessControlList, Privilege, WorkspaceLayout
from errors import ConfigurationError, StateError
from services.checking.authorization import Authorization
from services.checking.authorization_report import AuthorizationReport
from services.checking.checking_service import CheckingService
from stores.artifact_store import ArtifactLock, ArtifactStore, StepRef
from stores.session_log_store import Log, LogEntry, Outcome, Report, SessionLogStore
from utils.clock import Clock

_FUNCTION = "check-step-authorization"
_START_FUNCTION = "start-session"
_POSTCONDITIONS_FUNCTION = "check-step-postconditions"
_SESSION_UNREGISTERED = "session-unregistered"
# A lost compare-and-swap re-reads the lock and decides again, a bounded number of times.
_LOCK_CLAIM_ATTEMPTS = 3
_ALLOWED = "allowed"
_DENIED = "denied"

# `delete` is a forward declaration: roles may grant it, this function denies it
# until delete enforcement downstream of authorization is modelled (ACL principles).
_UNSUPPORTED_ACTIONS = frozenset({"delete"})


def _strip_fragment(artifact_path: Path) -> Path:
    """Drop any `#property` suffix: authorization is whole-resource (invariant 3)."""
    name = artifact_path.name
    return artifact_path.with_name(name.partition("#")[0]) if name else artifact_path


class StepAuthorizationChecker(CheckingService):
    """Decide one pending write against the framework's grants and the baseline.

    Spec (function 8): plain whole-resource RBAC over structured privileges,
    guarding every agent write live at the boundary — the same boundary that
    admits a write only inside an in-flight step instance (invariant 7), claims
    the written path's artifact lock for it (invariant 8), and refuses an
    unclaimed path whose staging baseline is not clean against `HEAD` (C6).
    """

    def __init__(
        self,
        session_log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        clock: Clock | None = None,
    ) -> None:
        """Create the checker over its log store, its grants, its layout, and Git."""
        super().__init__(session_log_store, clock)
        self._access_control_list = access_control_list
        self._workspace_layout = workspace_layout
        self._artifact_store = artifact_store

    def check_step_authorization(
        self,
        session_id: str,
        parent_session_id: str | None,
        artifact_path: Path,
        action: str,
    ) -> AuthorizationReport:
        """Authorize one pending write at the write-starting boundary."""
        return cast(
            AuthorizationReport,
            self._execute_check(
                session_id,
                parent_session_id,
                artifact_path=artifact_path,
                action=action,
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
        """Decide the write and journal the decision — allow or deny alike.

        Spec (function 8, Postconditions): one log entry per authorization
        decision; invariant 4: every deny is an ordinary `denied` outcome.
        """
        artifact_path: Path = request["artifact_path"]
        action: str = request["action"]
        actor = self._find_registered_actor(log)
        resource, failure_message = self._decide_authorization(
            actor, artifact_path, action, log
        )
        report = AuthorizationReport(
            context=self._build_context(
                _FUNCTION, session_id, parent_session_id, workflow_instance_id
            ),
            outcome=Outcome(
                status=_DENIED if failure_message is not None else _ALLOWED
            ),
            authorization=Authorization(
                actor=actor,
                artifact_path=artifact_path.as_posix(),
                action=action,
                resource=resource,
                failure_message=failure_message,
            ),
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
        return AuthorizationReport(
            context=self._build_context(
                _FUNCTION, session_id, parent_session_id, workflow_instance_id
            ),
            outcome=outcome,
        )

    def _find_registered_actor(self, log: Log) -> str:
        """Read the acting agent off the session's own registration entry.

        Spec (function 8, invariant 1): the actor is the AGENT derived from the
        registered host session, never the skill and never a function input.
        """
        for entry in log.entries:
            if entry.report.context.function == _START_FUNCTION:
                session = entry.report.payload.get("session")
                if session is not None:
                    return cast(str, session["agent"])
        raise StateError(
            "session-unregistered",
            f"Session '{log.session_id}' carries no registration naming its agent.",
            False,
        )

    def _decide_authorization(
        self, actor: str, artifact_path: Path, action: str, log: Log
    ) -> tuple[str | None, str | None]:
        """Answer the resource under test plus the denial cause, if there is one.

        Spec (function 8, invariant 4): the `failureMessage` names the cause —
        the missing privilege, the unsupported `delete`, the unresolvable
        resource, the logs path (invariant 6), the unclean baseline (invariant
        5), no step in flight (invariant 7), or the artifact lock another step
        instance holds (invariant 8). The two causes that ARE the absence of a
        resource answer none: naming one would be indistinguishable from a real
        answer.
        """
        target = _strip_fragment(artifact_path)
        ref = target.as_posix()
        if self._workspace_layout.is_logs_path(target):
            return None, (
                f"logs path: '{ref}' is harness-authored and single-writer (C0), "
                f"so no privilege can grant authorship of the journal"
            )
        try:
            resource = self._workspace_layout.resolve_resource(target)
        except ConfigurationError as failure:
            return None, f"unresolvable resource: {failure.message}"
        if action in _UNSUPPORTED_ACTIONS:
            return resource, (
                f"unsupported action: {action} {resource} is a forward declaration "
                f"the harness does not enforce yet"
            )
        privilege = Privilege(artifact=resource, action=action)
        if privilege not in self._access_control_list.list_privileges(actor):
            return resource, f"missing privilege: {action} {resource}"
        step_ref = self._find_in_flight_step_ref(log)
        if step_ref is None:
            return resource, (
                f"no step in flight: session '{log.session_id}' is not the worker "
                f"session of an in-flight step instance with its own step ref, so "
                f"it may not write '{ref}'"
            )
        return resource, self._claim_artifact_lock(ref, step_ref)

    def _claim_artifact_lock(self, ref: str, step_ref: StepRef) -> str | None:
        """Claim or re-point the path's artifact lock, answering the denial, if any.

        Spec (function 8, invariants 5 and 8): admitted when the claim succeeds
        on an unclaimed path (no lock, or one whose holder was promoted) whose
        staging baseline is clean against `HEAD`, when this step instance
        already holds the lock, or when an unpromoted step instance it continues
        — an earlier one of the same step in the same workflow instance — holds
        it, re-pointed by compare-and-swap. Any other holder denies.
        """
        for _ in range(_LOCK_CLAIM_ATTEMPTS):
            lock = self._artifact_store.find_artifact_lock(ref)
            if lock is not None and lock.holder == step_ref.name:
                return None
            released = lock is None or self._artifact_store.is_step_ref_promoted(
                lock.holder
            )
            if not released and not self._continues(step_ref, lock):
                return self._describe_held_lock(ref, lock)
            if released and not self._artifact_store.is_staging_clean(Path(ref)):
                return (
                    f"unclean staging baseline: '{ref}' is neither absent nor "
                    f"tracked-and-clean against HEAD"
                )
            if self._artifact_store.claim_artifact_lock(ref, step_ref, lock):
                return None
        lock = self._artifact_store.find_artifact_lock(ref)
        return self._describe_held_lock(ref, lock)

    @staticmethod
    def _continues(step_ref: StepRef, lock: ArtifactLock | None) -> bool:
        """Tell whether the lock's holder is a step instance this one continues (a retry)."""
        holder = None if lock is None else lock.holder_step_ref
        return (
            holder is not None
            and holder.workflow_instance_id == step_ref.workflow_instance_id
            and holder.step_slug == step_ref.step_slug
            and holder.session_id != step_ref.session_id
        )

    @staticmethod
    def _describe_held_lock(ref: str, lock: ArtifactLock | None) -> str:
        """Name the holder's workflow instance and step in the lock denial."""
        holder = None if lock is None else lock.holder_step_ref
        if holder is None:
            named = "another step instance" if lock is None else f"'{lock.holder}'"
            return f"artifact lock: '{ref}' is held by {named}"
        return (
            f"artifact lock: '{ref}' is held by step '{holder.step_slug}' of "
            f"workflow instance '{holder.workflow_instance_id}' (worker session "
            f"'{holder.session_id}')"
        )

    def _find_in_flight_step_ref(self, log: Log) -> StepRef | None:
        """Find the step ref of the in-flight step instance this session IS, if any.

        Spec (function 8, invariant 7): a write is admitted only from a worker
        session whose step ref exists and whose step instance has no function-10
        outcome yet — the dispatching session journals none after the worker
        session's registration.
        """
        registration = _find_registration(log)
        if registration is None:
            return None
        parent = registration.report.payload["session"].get("parentSessionId")
        if parent is None:
            return None
        step_ref = self._artifact_store.find_step_ref(log.session_id)
        if step_ref is None:
            return None
        try:
            parent_log = self._session_log_store.load_session_log(str(parent))
        except StateError as failure:
            if failure.code != _SESSION_UNREGISTERED:
                raise
            return None
        concluded = any(
            entry.report.context.function == _POSTCONDITIONS_FUNCTION
            and entry.timestamp >= registration.timestamp
            for entry in parent_log.entries
        )
        return None if concluded else step_ref


def _find_registration(log: Log) -> LogEntry | None:
    """Find the session's registration entry: its start entry naming its session."""
    for entry in log.entries:
        if (
            entry.report.context.function == _START_FUNCTION
            and entry.report.payload.get("session") is not None
        ):
            return entry
    return None


__all__ = ["StepAuthorizationChecker"]
