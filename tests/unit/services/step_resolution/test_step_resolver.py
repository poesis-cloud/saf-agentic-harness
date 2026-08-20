"""Unit tests for the step resolution service (harness function 3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from errors import InquiryError
from services.step_resolution import StepResolutionReport, StepResolver
from stores.session_log_store import Context, LogEntry, Outcome, SessionLogStore
from tests.unit.services.step_resolution.conftest import (
    FACILITATOR,
    INSTANCE_ID,
    ORCHESTRATOR_SESSION,
    STEP_SESSION,
    WORKFLOW_SLUG,
    FailingAppendJsonlStore,
    SequenceClock,
    append_ending,
    append_outcome,
    append_resolution,
    build_capabilities,
    build_catalog,
    build_step,
    read_entries,
    start_session_log,
)


def _build_resolver(
    log_store: SessionLogStore,
    *steps,
    clock: SequenceClock | None = None,
) -> StepResolver:
    """Build the service under test with real injected collaborators."""
    return StepResolver(
        session_log_store=log_store,
        workflow_catalog=build_catalog(*steps),
        clock=clock or SequenceClock("2026-08-17T15:00:00Z"),
    )


class TestStepResolver:
    """Cover function 3 — resolve-step — clause by clause."""

    def test_refuses_a_non_slug_session_id_without_building_a_report(
        self, log_store: SessionLogStore
    ) -> None:
        """Rule 1/4: a contract-invalid inquiry is `invalid-inquiry` and produces no report."""
        resolver = _build_resolver(log_store, build_step("review"))

        with pytest.raises(InquiryError) as raised:
            resolver.resolve_step("../../etc/passwd", None, WORKFLOW_SLUG)

        assert raised.value.code == "invalid-inquiry"
        assert raised.value.status == "inquiry-error"

    def test_refuses_an_ended_session_without_journaling(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """C8 / rule 3: an ended session is `state-error`/`session-ended`, never journaled."""
        start_session_log(log_store)
        append_ending(log_store)
        resolver = _build_resolver(log_store, build_step("review"))

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "state-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "session-ended"
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 2

    def test_reports_system_error_when_the_journal_append_fails(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Rule 1: a failing environment surfaces `system-error`; the entry is lost, not the report."""
        start_session_log(log_store)
        failing_store = SessionLogStore(workspace_dir, jsonl_store=FailingAppendJsonlStore())
        resolver = _build_resolver(failing_store, build_step("review"))

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "system-error"
        assert report.outcome.error is not None
        assert report.outcome.error.message
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 1

    def test_refuses_an_unknown_workflow_slug(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Precondition (E): `workflowSlug` names a catalog workflow — `unknown-workflow`, journaled."""
        start_session_log(log_store)
        resolver = _build_resolver(log_store, build_step("review"))

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, "no-such-workflow")

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "unknown-workflow"
        assert report.context.workflow_instance_id is None
        journaled = read_entries(workspace_dir, ORCHESTRATOR_SESSION)
        assert len(journaled) == 2
        assert journaled[-1]["report"]["outcome"]["error"]["code"] == "unknown-workflow"

    def test_refuses_a_session_agent_that_does_not_facilitate_the_workflow(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Precondition (E): the session's agent is the facilitator — `not-facilitator`, journaled."""
        start_session_log(log_store, agent="qa-engineer")
        resolver = _build_resolver(log_store, build_step("review"))

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "not-facilitator"
        journaled = read_entries(workspace_dir, ORCHESTRATOR_SESSION)
        assert len(journaled) == 2
        assert journaled[-1]["report"]["outcome"]["error"]["code"] == "not-facilitator"

    def test_refuses_an_unregistered_session_with_a_report_it_cannot_journal(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Precondition (E) / rule 4: `session-unregistered` returns a report with no log to journal to."""
        resolver = _build_resolver(log_store, build_step("review"))

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "session-unregistered"
        assert read_entries(workspace_dir, ORCHESTRATOR_SESSION) == ()

    def test_drops_a_reopened_step_back_out_of_the_executed_set(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 1: a step counts executed only while its LATEST function 10 outcome passes."""
        start_session_log(log_store)
        start_session_log(log_store, session_id=STEP_SESSION, agent="qa-engineer")
        append_resolution(log_store, "2026-08-17T13:01:00Z", "build")
        append_outcome(log_store, "2026-08-17T13:02:00Z", "pass")
        append_resolution(log_store, "2026-08-17T13:03:00Z", "review")
        append_outcome(log_store, "2026-08-17T13:04:00Z", "pass")
        append_resolution(log_store, "2026-08-17T13:05:00Z", "build")
        append_outcome(log_store, "2026-08-17T13:06:00Z", "fail")
        resolver = _build_resolver(
            log_store,
            build_step("build"),
            build_step("review", predecessors=("build",)),
        )

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "step-resolution"
        assert report.step is not None
        assert report.step.slug == "build"

    def test_resolves_the_first_remaining_step_whose_predecessors_are_executed(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 2: eligibility is the first remaining step with executed precondition predecessors."""
        start_session_log(log_store)
        resolver = _build_resolver(
            log_store,
            build_step("review", predecessors=("build",)),
            build_step("build"),
        )

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.step is not None
        assert report.step.slug == "build"

    def test_advances_to_the_successor_once_its_predecessor_is_executed(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 2: authored order plus executed predecessors move the cursor forward."""
        start_session_log(log_store)
        start_session_log(log_store, session_id=STEP_SESSION, agent="qa-engineer")
        append_resolution(log_store, "2026-08-17T13:01:00Z", "build")
        append_outcome(log_store, "2026-08-17T13:02:00Z", "pass")
        resolver = _build_resolver(
            log_store,
            build_step("build"),
            build_step("review", predecessors=("build",)),
            build_step("publish", predecessors=("review",)),
        )

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.step is not None
        assert report.step.slug == "review"

    def test_carries_no_model_profile_in_the_resolution(self, log_store: SessionLogStore) -> None:
        """Invariant 3: the model profile is function 4's — the two functions are independent."""
        start_session_log(log_store)
        resolver = _build_resolver(log_store, build_step("review"))

        rendered = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG).to_dict()

        assert "profile" not in rendered
        assert "profile" not in rendered["step"]

    def test_writes_no_artifact_and_starts_no_step(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Invariant 4 / postcondition: nothing beyond the one log entry changes."""
        start_session_log(log_store)
        before = {path for path in workspace_dir.rglob("*") if path.is_file()}
        resolver = _build_resolver(log_store, build_step("review"))

        resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        after = {path for path in workspace_dir.rglob("*") if path.is_file()}
        assert after == before
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 2

    def test_observes_no_next_step_without_declaring_completion(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 5: every step executed is the reversible `no-next-step` observation, no verdict."""
        start_session_log(log_store)
        start_session_log(log_store, session_id=STEP_SESSION, agent="qa-engineer")
        append_resolution(log_store, "2026-08-17T13:01:00Z", "review")
        append_outcome(log_store, "2026-08-17T13:02:00Z", "pass")
        resolver = _build_resolver(log_store, build_step("review"))

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "no-next-step"
        assert report.step is None
        assert report.context.workflow_instance_id == INSTANCE_ID

    def test_returns_the_failed_step_again_on_plain_forward_resolution(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 6: retry is re-resolution — no `step` parameter, no `previous` direction."""
        start_session_log(log_store)
        start_session_log(log_store, session_id=STEP_SESSION, agent="qa-engineer")
        append_resolution(log_store, "2026-08-17T13:01:00Z", "review")
        append_outcome(log_store, "2026-08-17T13:02:00Z", "fail")
        resolver = _build_resolver(log_store, build_step("review"))

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.step is not None
        assert report.step.slug == "review"

    def test_returns_the_step_alone_with_no_step_level_user_surface(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 7: the report is `outcome` ± `step`, nothing more — no in-band gate."""
        start_session_log(log_store)
        resolver = _build_resolver(log_store, build_step("review"))

        rendered = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG).to_dict()

        assert set(rendered) == {"context", "outcome", "step"}

    def test_continues_the_latest_open_instance_and_abandons_older_ones_silently(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Invariant 8: latest-open-wins; an older open instance is simply no longer the latest."""
        start_session_log(log_store, session_id="driver-old", agent=FACILITATOR)
        start_session_log(log_store, session_id="driver-new", agent=FACILITATOR)
        start_session_log(log_store, session_id=STEP_SESSION, agent="qa-engineer")
        start_session_log(log_store)
        append_resolution(
            log_store,
            "2026-08-17T13:01:00Z",
            "build",
            session_id="driver-old",
            workflow_instance_id="verification-0AAAAA",
        )
        append_outcome(
            log_store,
            "2026-08-17T13:02:00Z",
            "pass",
            workflow_instance_id="verification-0AAAAA",
        )
        append_resolution(
            log_store,
            "2026-08-17T13:05:00Z",
            "build",
            session_id="driver-new",
            workflow_instance_id="verification-0ZZZZZ",
        )
        append_outcome(
            log_store,
            "2026-08-17T13:06:00Z",
            "pass",
            workflow_instance_id="verification-0ZZZZZ",
        )
        resolver = _build_resolver(
            log_store,
            build_step("build"),
            build_step("review", predecessors=("build",)),
        )

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "step-resolution"
        assert report.context.workflow_instance_id == "verification-0ZZZZZ"
        assert len(read_entries(workspace_dir, "driver-old")) == 2

    def test_breaks_an_instance_timestamp_tie_on_the_lowest_instance_id(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 8: an identical latest `timestamp` breaks to the lexicographically lowest id."""
        start_session_log(log_store, session_id="driver-a", agent=FACILITATOR)
        start_session_log(log_store, session_id="driver-b", agent=FACILITATOR)
        start_session_log(log_store, session_id=STEP_SESSION, agent="qa-engineer")
        start_session_log(log_store)
        for driver, instance in (
            ("driver-a", "verification-0BBBBB"),
            ("driver-b", "verification-0AAAAA"),
        ):
            append_resolution(
                log_store,
                "2026-08-17T13:04:00Z",
                "build",
                session_id=driver,
                workflow_instance_id=instance,
            )
            append_outcome(
                log_store,
                "2026-08-17T13:05:00Z",
                "pass",
                workflow_instance_id=instance,
            )
        resolver = _build_resolver(
            log_store,
            build_step("build"),
            build_step("review", predecessors=("build",)),
        )

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "step-resolution"
        assert report.context.workflow_instance_id == "verification-0AAAAA"

    def test_refuses_a_call_arriving_while_a_step_is_in_flight(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Invariant 9: a resolution with no journaled outcome is `state-error`/`step-in-flight`, journaled."""
        start_session_log(log_store)
        append_resolution(log_store, "2026-08-17T13:01:00Z", "build")
        resolver = _build_resolver(
            log_store,
            build_step("build"),
            build_step("review", predecessors=("build",)),
        )

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.outcome.status == "state-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "step-in-flight"
        assert report.context.workflow_instance_id == INSTANCE_ID
        journaled = read_entries(workspace_dir, ORCHESTRATOR_SESSION)
        assert journaled[-1]["report"]["outcome"]["error"]["code"] == "step-in-flight"

    def test_mints_a_new_instance_when_none_is_open(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Interface / postcondition: none open opens one; the opening IS this invocation's entry."""
        start_session_log(log_store)
        resolver = _build_resolver(log_store, build_step("review"))

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        instance_id = report.context.workflow_instance_id
        assert instance_id is not None
        assert instance_id.startswith(f"{WORKFLOW_SLUG}-")
        mint = instance_id[len(WORKFLOW_SLUG) + 1 :]
        assert len(mint) >= 4
        assert set(mint) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        journaled = read_entries(workspace_dir, ORCHESTRATOR_SESSION)
        assert journaled[-1]["report"]["context"]["workflowInstanceId"] == instance_id

    def test_resolves_the_configured_step_verbatim(self, log_store: SessionLogStore) -> None:
        """Interface (worked example): the resolution IS the configured step object, verbatim."""
        start_session_log(log_store)
        start_session_log(log_store, session_id=STEP_SESSION, agent="qa-engineer")
        append_resolution(log_store, "2026-08-17T13:01:00Z", "build")
        append_outcome(log_store, "2026-08-17T13:02:00Z", "pass")
        weights = build_capabilities(deep_reasoning=9.0, coding=2.0, tool_use=6.0)
        configured = build_step(
            "review",
            actor="qa-engineer",
            artifact="review-report",
            predecessors=("build",),
            weights=weights,
            skills=("code-review",),
            instructions=("review",),
        )
        resolver = _build_resolver(log_store, build_step("build"), configured)

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        assert report.step is configured
        rendered = report.to_dict()["step"]
        assert rendered["slug"] == "review"
        assert rendered["actor"] == "qa-engineer"
        assert rendered["artifact"] == "review-report"
        assert rendered["skills"] == ["code-review"]
        assert rendered["instructions"] == ["review"]
        assert rendered["capabilities"] == dict(weights)
        assert rendered["conditions"] == [
            {"kind": "precondition", "slug": "after-build", "step": "build"}
        ]

    def test_journals_exactly_one_entry_carrying_the_returned_report(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Postcondition / logging: 1 invocation = 1 entry, and the entry IS the report."""
        start_session_log(log_store)
        resolver = _build_resolver(
            log_store,
            build_step("review"),
            clock=SequenceClock("2026-08-17T15:00:00Z"),
        )

        report = resolver.resolve_step(ORCHESTRATOR_SESSION, None, WORKFLOW_SLUG)

        journaled = read_entries(workspace_dir, ORCHESTRATOR_SESSION)
        assert len(journaled) == 2
        entry = LogEntry.from_dict(journaled[-1])
        assert entry.timestamp == "2026-08-17T15:00:00Z"
        assert entry.report.to_dict() == report.to_dict()


class TestStepResolutionReport:
    """Cover the report the step resolution service returns."""

    def test_renders_the_no_next_step_report_without_a_step_property(self) -> None:
        """Interface: `no-next-step` carries no step — `outcome` ± `step`, nothing more."""
        report = StepResolutionReport(
            context=Context(function="resolve-step", session_id=ORCHESTRATOR_SESSION),
            outcome=Outcome(status="no-next-step"),
        )

        assert report.to_dict() == {
            "context": {
                "function": "resolve-step",
                "sessionId": ORCHESTRATOR_SESSION,
                "parentSessionId": None,
                "workflowInstanceId": None,
            },
            "outcome": {"status": "no-next-step"},
        }
