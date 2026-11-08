"""The derived, cross-log view of one workflow instance."""

from __future__ import annotations

from dataclasses import dataclass

from stores.session_log_store.log_entry import LogEntry

_RESOLVE_STEP_FUNCTION = "resolve-step"
_STEP_RESOLUTION_STATUS = "step-resolution"
_POSTCONDITIONS_FUNCTION = "check-step-postconditions"
_EXECUTED_STATUS = "pass"


def _is_step_resolution(entry: LogEntry) -> bool:
    """Tell whether the entry journals a step resolution."""
    return (
        entry.report.context.function == _RESOLVE_STEP_FUNCTION
        and entry.report.outcome.status == _STEP_RESOLUTION_STATUS
    )


def _is_postcondition_outcome(entry: LogEntry) -> bool:
    """Tell whether the entry journals a step's postcondition outcome."""
    return entry.report.context.function == _POSTCONDITIONS_FUNCTION


@dataclass(frozen=True)
class WorkflowInstanceView:
    """One workflow instance's timestamp-ordered entries, assembled, never persisted.

    A `check-step-postconditions` entry carries no step slug of its own: under the
    single-driver invariant it correlates to the most recent preceding step
    resolution in the instance's timestamp order.
    """

    workflow_instance_id: str
    entries: tuple[LogEntry, ...]

    def list_executed_steps(self) -> frozenset[str]:
        """List the steps whose latest journaled postcondition outcome passes."""
        return frozenset(
            step_slug
            for step_slug, entry in self._map_latest_outcomes().items()
            if entry.report.outcome.status == _EXECUTED_STATUS
        )

    def find_latest_outcome(self, step_slug: str) -> LogEntry | None:
        """Find the step's latest journaled postcondition outcome entry, if any."""
        return self._map_latest_outcomes().get(step_slug)

    def find_unresolved_step_resolution(self, actor: str) -> LogEntry | None:
        """Find the actor's in-flight step resolution — resolved, no outcome yet."""
        pending: LogEntry | None = None
        for entry in self.entries:
            if _is_step_resolution(entry):
                pending = entry
            elif _is_postcondition_outcome(entry):
                pending = None
        if pending is not None and pending.report.payload["step"]["actor"] == actor:
            return pending
        return None

    def _map_latest_outcomes(self) -> dict[str, LogEntry]:
        """Map each resolved step slug to its latest postcondition outcome entry."""
        latest_outcomes: dict[str, LogEntry] = {}
        step_in_flight: str | None = None
        for entry in self.entries:
            if _is_step_resolution(entry):
                step_in_flight = entry.report.payload["step"]["slug"]
            elif _is_postcondition_outcome(entry) and step_in_flight is not None:
                latest_outcomes[step_in_flight] = entry
        return latest_outcomes


__all__ = ["WorkflowInstanceView"]
