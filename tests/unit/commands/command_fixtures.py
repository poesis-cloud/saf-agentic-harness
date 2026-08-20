"""Shared builders for the command-layer tests: reports and recording fakes.

Isolation comes from constructor injection only — every fake below is handed to a
command through its constructor, never monkey-patched in (spec, "Unit testing").
"""

from __future__ import annotations

from typing import Any, Mapping

from stores.session_log_store.context import Context
from stores.session_log_store.outcome import Outcome
from stores.session_log_store.report import Report

SESSION_ID = "s1"
PARENT_SESSION_ID = "p1"


def build_report(function: str, status: str = "resolved", **payload: Any) -> Report:
    """Build the report a fake service answers with."""
    return Report(
        context=Context(
            function=function,
            session_id=SESSION_ID,
            parent_session_id=None,
            workflow_instance_id=None,
        ),
        outcome=Outcome(status=status),
        payload=payload,
    )


class RecordingService:
    """Record the typed parameters a command unpacked its inquiry into.

    Spec (Classes, `commands`): services never receive an `Inquiry` — the command
    unpacks it into typed parameters, so nothing beneath `commands` ever depends
    upward on it. This fake asserts exactly that boundary.
    """

    def __init__(self, function: str, status: str = "resolved") -> None:
        """Create the fake over the report it answers with."""
        self.calls: list[Mapping[str, Any]] = []
        self.report = build_report(function, status)

    def _record_call(self, **parameters: Any) -> Report:
        """Record one service call and answer the canned report."""
        self.calls.append(parameters)
        return self.report

    def start_session(
        self, agent: str, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Stand in for `SessionLifecycle.start_session` (function 0)."""
        return self._record_call(
            agent=agent, session_id=session_id, parent_session_id=parent_session_id
        )

    def end_session(self, session_id: str) -> Report:
        """Stand in for `SessionLifecycle.end_session` (function 11)."""
        return self._record_call(session_id=session_id)

    def resolve_workflow_instructions(
        self, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Stand in for `WorkflowInstructionResolver` (function 1)."""
        return self._record_call(
            session_id=session_id, parent_session_id=parent_session_id
        )

    def resolve_workflow_skills(
        self, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Stand in for `WorkflowSkillResolver` (function 2)."""
        return self._record_call(
            session_id=session_id, parent_session_id=parent_session_id
        )

    def resolve_step(
        self, session_id: str, parent_session_id: str | None, workflow_slug: str
    ) -> Report:
        """Stand in for `StepResolver.resolve_step` (function 3)."""
        return self._record_call(
            session_id=session_id,
            parent_session_id=parent_session_id,
            workflow_slug=workflow_slug,
        )

    def resolve_step_model(
        self, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Stand in for `StepModelResolver` (function 4)."""
        return self._record_call(
            session_id=session_id, parent_session_id=parent_session_id
        )

    def check_step_preconditions(
        self, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Stand in for `StepPreconditionChecker` (function 5)."""
        return self._record_call(
            session_id=session_id, parent_session_id=parent_session_id
        )

    def resolve_step_instructions(
        self, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Stand in for `StepInstructionResolver` (function 6)."""
        return self._record_call(
            session_id=session_id, parent_session_id=parent_session_id
        )

    def resolve_step_skills(
        self, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Stand in for `StepSkillResolver` (function 7)."""
        return self._record_call(
            session_id=session_id, parent_session_id=parent_session_id
        )

    def check_step_authorization(
        self,
        session_id: str,
        parent_session_id: str | None,
        artifact_path: Any,
        action: str,
    ) -> Report:
        """Stand in for `StepAuthorizationChecker` (function 8)."""
        return self._record_call(
            session_id=session_id,
            parent_session_id=parent_session_id,
            artifact_path=artifact_path,
            action=action,
        )

    def check_step_artifact(
        self,
        session_id: str,
        parent_session_id: str | None,
        artifact_paths: Any,
    ) -> Report:
        """Stand in for `StepArtifactChecker` (function 9)."""
        return self._record_call(
            session_id=session_id,
            parent_session_id=parent_session_id,
            artifact_paths=artifact_paths,
        )

    def check_step_postconditions(
        self, session_id: str, parent_session_id: str | None
    ) -> Report:
        """Stand in for `StepPostconditionChecker` (function 10)."""
        return self._record_call(
            session_id=session_id, parent_session_id=parent_session_id
        )


__all__ = ["PARENT_SESSION_ID", "RecordingService", "SESSION_ID", "build_report"]
