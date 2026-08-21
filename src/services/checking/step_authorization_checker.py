"""Function 8 — `check-step-authorization`: is this write a granted privilege?"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, cast

from config import AccessControlList, Privilege, WorkspaceLayout
from errors import ConfigurationError, InquiryError, StateError
from services.checking.authorization import Authorization
from services.checking.authorization_report import AuthorizationReport
from services.checking.checking_service import CheckingService
from stores.artifact_store import ArtifactStore
from stores.session_log_store import Log, Outcome, Report, SessionLogStore

_FUNCTION = "check-step-authorization"
_START_FUNCTION = "start-session"
_ALLOWED = "allowed"
_DENIED = "denied"
_ACTIONS = frozenset({"create", "update", "delete"})

# `delete` is a forward declaration: roles may grant it, this function denies it
# until delete enforcement downstream of authorization is modelled (ACL principles).
_UNSUPPORTED_ACTIONS = frozenset({"delete"})

# The contract requires a `resource` slug on every decision branch, including the
# denies whose very cause is that no artifact schema resolves the path.
_UNRESOLVED_RESOURCE = "unresolved"


def _strip_fragment(artifact_path: Path) -> Path:
    """Drop any `#property` suffix: authorization is whole-resource (invariant 3)."""
    name = artifact_path.name
    return artifact_path.with_name(name.partition("#")[0]) if name else artifact_path


class StepAuthorizationChecker(CheckingService):
    """Decide one pending write against the framework's grants and the baseline.

    Spec (function 8): plain whole-resource RBAC over structured privileges,
    guarding every agent write live at the boundary — the same boundary that
    refuses writes whose staging baseline is not clean against `HEAD` (C6).
    """

    def __init__(
        self,
        session_log_store: SessionLogStore,
        access_control_list: AccessControlList,
        workspace_layout: WorkspaceLayout,
        artifact_store: ArtifactStore,
        clock: Callable[[], str] | None = None,
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
        self._require_known_action(action)
        actor = self._find_registered_actor(log)
        resource, failure_message = self._decide_authorization(
            actor, artifact_path, action
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

    def _require_known_action(self, action: str) -> None:
        """Enforce that the host write tool mapped onto the contract vocabulary.

        Spec (function 8, precondition (E)): violation is `inquiry-error`
        (`unknown-action`), journaled.
        """
        if action not in _ACTIONS:
            raise InquiryError(
                "unknown-action",
                f"Action '{action}' is outside the contract vocabulary "
                f"({', '.join(sorted(_ACTIONS))}).",
                False,
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
        self, actor: str, artifact_path: Path, action: str
    ) -> tuple[str, str | None]:
        """Answer the resource under test plus the denial cause, if there is one.

        Spec (function 8, invariant 4): the `failureMessage` names the cause —
        the missing privilege, the unsupported `delete`, the unresolvable
        resource, the logs path (invariant 6), or the unclean baseline
        (invariant 5).
        """
        target = _strip_fragment(artifact_path)
        ref = target.as_posix()
        if self._workspace_layout.is_logs_path(target):
            return _UNRESOLVED_RESOURCE, (
                f"logs path: '{ref}' is harness-authored and single-writer (C0), "
                f"so no privilege can grant authorship of the journal"
            )
        try:
            resource = self._workspace_layout.resolve_resource(target, None)
        except ConfigurationError as failure:
            return _UNRESOLVED_RESOURCE, f"unresolvable resource: {failure.message}"
        if action in _UNSUPPORTED_ACTIONS:
            return resource, (
                f"unsupported action: {action} {resource} is a forward declaration "
                f"the harness does not enforce yet"
            )
        privilege = Privilege(artifact=resource, action=action)
        if privilege not in self._access_control_list.list_privileges(actor):
            return resource, f"missing privilege: {action} {resource}"
        if not self._artifact_store.is_staging_clean(target):
            return resource, (
                f"unclean staging baseline: '{ref}' is neither absent nor "
                f"tracked-and-clean against HEAD"
            )
        return resource, None


__all__ = ["StepAuthorizationChecker"]
