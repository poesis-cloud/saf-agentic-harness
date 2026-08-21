"""The model binding core: which profile serves the in-flight step (function 4)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Callable

from config import ModelProfiles, Step, WorkflowCatalog
from errors import ConfigurationError, HarnessError, InquiryError, StateError, SystemFailureError
from services.model_resolution.model_profile_binding import ModelProfileBinding
from services.model_resolution.model_profile_report import ModelProfileReport
from stores.session_log_store import (
    Context,
    Error,
    Log,
    LogEntry,
    Outcome,
    SessionLogStore,
)

_FUNCTION = "resolve-step-model"
_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")
_END_FUNCTION = "end-session"
_ENDED_STATUS = "ended"
_RESOLVE_STEP_FUNCTION = "resolve-step"
_STEP_RESOLUTION_STATUS = "step-resolution"
_POSTCONDITIONS_FUNCTION = "check-step-postconditions"
_NOT_APPLICABLE_STATUS = "not-applicable"
_RESOLVED_STATUS = "resolved"


def _utc_timestamp() -> str:
    """Read the wall-clock time a log entry is appended."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _require_inquiry_slugs(session_id: str, parent_session_id: str | None) -> None:
    """Reject session attribution the contract cannot admit — a session id becomes a filename."""
    for value in (session_id, parent_session_id):
        if value is None:
            continue
        if not isinstance(value, str) or _SLUG_PATTERN.match(value) is None:
            raise InquiryError(
                "invalid-inquiry",
                f"Session attribution '{value}' is not a safe slug.",
                False,
            )


def _has_ending_entry(log: Log) -> bool:
    """Tell whether the session's log carries function 11's ending entry (C8)."""
    return any(
        entry.report.context.function == _END_FUNCTION
        and entry.report.outcome.status == _ENDED_STATUS
        for entry in log.entries
    )


def _render_error_report(context: Context, failure: HarnessError) -> ModelProfileReport:
    """Render one failure as its contract report — status from the exception type."""
    return ModelProfileReport(
        context=context,
        outcome=Outcome(
            status=failure.status,
            error=Error(code=failure.code, message=failure.message, retryable=failure.retryable),
        ),
    )


class StepModelResolver:
    """Bind the in-flight step to the model profile its capability demand scores highest.

    Spec (function 4): independent of function 3 and of the acting agent — the profile is
    a pure function of static configuration and the step deduced from the session's logs.
    """

    def __init__(
        self,
        session_log_store: SessionLogStore,
        workflow_catalog: WorkflowCatalog,
        model_profiles: ModelProfiles,
        clock: Callable[[], str] | None = None,
    ) -> None:
        """Create the resolver over its injected store, catalog, profiles, and clock."""
        self._session_log_store = session_log_store
        self._catalog = workflow_catalog
        self._model_profiles = model_profiles
        self._clock = clock or _utc_timestamp

    def resolve_step_model(
        self, session_id: str, parent_session_id: str | None
    ) -> ModelProfileReport:
        """Resolve the model profile serving this session's in-flight step."""
        _require_inquiry_slugs(session_id, parent_session_id)
        context = Context(
            function=_FUNCTION,
            session_id=session_id,
            parent_session_id=parent_session_id,
        )

        try:
            log = self._load_open_session_log(session_id)
        except HarnessError as failure:
            return _render_error_report(context, failure)

        resolution = self._find_in_flight_resolution(log)
        if resolution is None:
            return ModelProfileReport(
                context=context, outcome=Outcome(status=_NOT_APPLICABLE_STATUS)
            )

        instance_id = resolution.report.context.workflow_instance_id
        context = Context(
            function=_FUNCTION,
            session_id=session_id,
            parent_session_id=parent_session_id,
            workflow_instance_id=instance_id,
        )
        try:
            step = self._find_step(instance_id, resolution)
            binding = self._bind_model_profile(step)
        except HarnessError as failure:
            return self._journal_report(_render_error_report(context, failure))

        return self._journal_report(
            ModelProfileReport(
                context=context, outcome=Outcome(status=_RESOLVED_STATUS), profile=binding
            )
        )

    def _load_open_session_log(self, session_id: str) -> Log:
        """Load the attributed session's log, refusing an unregistered or ended session."""
        try:
            log = self._session_log_store.load_session_log(session_id)
        except StateError as failure:  # the store's only failure here is a missing log
            raise InquiryError("session-unregistered", failure.message, False) from failure
        if _has_ending_entry(log):
            raise StateError(
                "session-ended",
                f"Session '{session_id}' carries an ending entry and accepts no invocation.",
                False,
            )
        return log

    def _find_in_flight_resolution(self, log: Log) -> LogEntry | None:
        """Find the session's in-flight step resolution — resolved, no outcome yet.

        Spec (function 4, precondition E): the target is an in-flight step "in the
        invoking session". Function 10's outcome is "appended to the dispatching
        (orchestrator) session's log" — the same log this resolution lives in — so this
        log alone decides, and a sibling session's pending step is never ours.
        """
        pending: LogEntry | None = None
        for entry in log.entries:
            function = entry.report.context.function
            if (
                function == _RESOLVE_STEP_FUNCTION
                and entry.report.outcome.status == _STEP_RESOLUTION_STATUS
            ):
                pending = entry
            elif function == _POSTCONDITIONS_FUNCTION:
                pending = None
        return pending

    def _find_step(self, workflow_instance_id: str | None, resolution: LogEntry) -> Step:
        """Read the in-flight step's declaration from the workflow configuration.

        Spec (invariant 1): the weights are the step's own static declaration — read from
        configuration at use time, never from the journaled dispatch payload.
        """
        step_slug = resolution.report.payload["step"]["slug"]
        if workflow_instance_id is None:
            raise StateError(
                "step-correlation-missing",
                "The in-flight step resolution names no workflow instance.",
                False,
            )
        workflow = self._catalog.find_workflow(workflow_instance_id.rsplit("-", 1)[0])
        for step in workflow.steps:
            if step.slug == step_slug:
                return step
        raise ConfigurationError(
            "unknown-step",
            f"Workflow '{workflow.slug}' declares no step '{step_slug}'.",
            False,
        )

    def _bind_model_profile(self, step: Step) -> ModelProfileBinding:
        """Pick the highest-scoring profile, breaking ties on cost rank then slug.

        Spec (invariant 3): highest score wins; an exact tie breaks toward the lower
        `costRank`, and a further tie toward the lexicographically lowest slug.
        """
        if not self._model_profiles.profiles:
            raise ConfigurationError(
                "empty-model-catalog",
                "The model catalog declares no profile to route to.",
                False,
            )
        ranked = sorted(
            (
                (
                    -self._model_profiles.score_model(slug, step.capabilities),
                    profile.cost_rank,
                    slug,
                )
                for slug, profile in self._model_profiles.profiles.items()
            )
        )
        negated_score, cost_rank, slug = ranked[0]
        return ModelProfileBinding(
            slug=slug,
            score=-negated_score,
            cost_rank=cost_rank,
            reason=(
                f"highest weighted capability score ({-negated_score}) for step "
                f"'{step.slug}'"
            ),
        )

    def _journal_report(self, report: ModelProfileReport) -> ModelProfileReport:
        """Append this invocation's single entry; a failing append surfaces `system-error`.

        Spec (rule 4): a completed invocation whose log append fails "still returns its
        report" — the entry is lost, never the profile the caller was promised.
        """
        entry = LogEntry(timestamp=self._clock(), report=report)
        try:
            self._session_log_store.append_log_entry(report.context.session_id, entry)
        except (OSError, HarnessError) as failure:
            lost = SystemFailureError("log-append-failed", str(failure), True)
            return ModelProfileReport(
                context=report.context,
                outcome=Outcome(
                    status=lost.status,
                    error=Error(code=lost.code, message=lost.message, retryable=lost.retryable),
                ),
                profile=report.profile,
            )
        return report


__all__ = ["StepModelResolver"]
