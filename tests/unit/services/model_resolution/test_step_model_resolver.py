"""Unit tests for the model resolution service (harness function 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from errors import ConfigurationError, InquiryError, SystemFailureError
from services.model_resolution import ModelProfileBinding, ModelProfileReport, StepModelResolver
from stores.session_log_store import Context, LogEntry, Outcome, SessionLogStore
from tests.unit.services.model_resolution.conftest import (
    INSTANCE_ID,
    ORCHESTRATOR_SESSION,
    STEP_SLUG,
    FailingAppendJsonlStore,
    SequenceClock,
    append_ending,
    append_outcome,
    append_resolution,
    build_capabilities,
    build_catalog,
    build_profiles,
    build_step,
    read_entries,
    start_session_log,
    write_unroutable_workflow,
)

_DEEP_NINE = build_capabilities(deep_reasoning=9.0)
_DEEP_SEVEN = build_capabilities(deep_reasoning=7.0)
_SECOND_ORCHESTRATOR_SESSION = "orchestrator-2"


class FailingInstanceViewSessionLogStore(SessionLogStore):
    """Fake a store whose cross-log instance view cannot be assembled."""

    def load_workflow_instance_view(self, workflow_instance_id: str):
        """Fail the derived read the way an unreadable sibling log does."""
        raise SystemFailureError(
            "instance-view-unreadable", "The instance view cannot be assembled.", True
        )


def _build_resolver(
    log_store: SessionLogStore,
    *,
    step=None,
    profiles=None,
    clock: SequenceClock | None = None,
) -> StepModelResolver:
    """Build the service under test with real injected collaborators."""
    return StepModelResolver(
        session_log_store=log_store,
        workflow_catalog=build_catalog(step or build_step()),
        model_profiles=profiles
        or build_profiles(("model-a", 2, _DEEP_NINE), ("model-b", 1, _DEEP_SEVEN)),
        clock=clock or SequenceClock("2026-08-17T15:00:00Z"),
    )


class TestStepModelResolver:
    """Cover function 4 — resolve-step-model — clause by clause."""

    def test_refuses_a_non_slug_session_id_without_building_a_report(
        self, log_store: SessionLogStore
    ) -> None:
        """Rule 1/4: a contract-invalid inquiry is `invalid-inquiry` and produces no report."""
        resolver = _build_resolver(log_store)

        with pytest.raises(InquiryError) as raised:
            resolver.resolve_step_model("../../etc/passwd", None)

        assert raised.value.code == "invalid-inquiry"
        assert raised.value.status == "inquiry-error"

    def test_refuses_an_ended_session_without_journaling(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """C8 / rule 3: an ended session is `state-error`/`session-ended`, never journaled."""
        start_session_log(log_store)
        append_resolution(log_store)
        append_ending(log_store)
        resolver = _build_resolver(log_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "state-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "session-ended"
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 3

    def test_reports_system_error_when_the_journal_append_fails(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Rule 4: a completed invocation whose log append fails STILL RETURNS ITS REPORT and
        surfaces `system-error` — the entry is lost, the resolved profile is not."""
        start_session_log(log_store)
        append_resolution(log_store)
        failing_store = SessionLogStore(workspace_dir, jsonl_store=FailingAppendJsonlStore())
        resolver = _build_resolver(failing_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "system-error"
        assert report.outcome.error is not None
        assert report.outcome.error.message
        assert report.profile is not None
        assert report.profile.slug == "model-a"
        assert report.to_dict()["profile"]["slug"] == "model-a"
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 2

    def test_answers_not_applicable_when_no_step_is_in_flight(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Precondition (E) / rule 2: no in-flight step names no target — `not-applicable`, never journaled."""
        start_session_log(log_store)
        resolver = _build_resolver(log_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "not-applicable"
        assert report.outcome.error is None
        assert report.profile is None
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 1

    def test_answers_not_applicable_after_the_step_outcome_journaled(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Rule 2: after the first function 10 outcome the step is no longer in flight — re-delivery
        finds no target. Function 10, Postconditions: that outcome is "appended to the dispatching
        (orchestrator) session's log", so THIS session's own log concludes the step."""
        start_session_log(log_store)
        append_resolution(log_store)
        append_outcome(log_store)
        resolver = _build_resolver(log_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "not-applicable"
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 3

    def test_ignores_a_pending_resolution_another_session_holds_in_the_same_instance(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Precondition (E): the in-flight step is one "in the INVOKING session" — this session's
        step concluded, so a sibling session's still-pending resolution in the same instance is no
        target of ours: `not-applicable` (rule 2)."""
        start_session_log(log_store)
        append_resolution(log_store, timestamp="2026-08-17T13:01:00Z")
        append_outcome(log_store, timestamp="2026-08-17T13:02:00Z")
        start_session_log(
            log_store, session_id=_SECOND_ORCHESTRATOR_SESSION, timestamp="2026-08-17T13:03:00Z"
        )
        append_resolution(
            log_store,
            timestamp="2026-08-17T13:04:00Z",
            session_id=_SECOND_ORCHESTRATOR_SESSION,
        )
        resolver = _build_resolver(log_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "not-applicable"
        assert report.profile is None
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 3

    def test_resolves_this_session_s_step_though_another_actor_holds_the_instance_s_latest(
        self, log_store: SessionLogStore
    ) -> None:
        """Precondition (E): an in-flight step "in the invoking session" is the target — the
        deduction reads this session's logs (Interface), never an instance-wide, actor-filtered
        query that a later sibling resolution would answer for."""
        start_session_log(log_store)
        append_resolution(log_store, timestamp="2026-08-17T13:01:00Z")
        start_session_log(
            log_store, session_id=_SECOND_ORCHESTRATOR_SESSION, timestamp="2026-08-17T13:02:00Z"
        )
        append_resolution(
            log_store,
            timestamp="2026-08-17T13:03:00Z",
            session_id=_SECOND_ORCHESTRATOR_SESSION,
            actor="developer",
        )
        resolver = _build_resolver(log_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "resolved"
        assert report.profile is not None
        assert report.context.workflow_instance_id == INSTANCE_ID

    def test_never_lets_an_instance_view_failure_cross_the_public_method(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Classes: "no exception ever crosses the command boundary" — and function 4 deduces its
        step "from its own logs" (Interface), so an unreadable cross-log instance view cannot even
        reach it."""
        start_session_log(log_store)
        append_resolution(log_store)
        resolver = _build_resolver(
            FailingInstanceViewSessionLogStore(workspace_dir),
        )

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert isinstance(report, ModelProfileReport)
        assert report.outcome.status == "resolved"
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 3

    def test_reports_state_error_when_the_in_flight_resolution_names_no_instance(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Rule 1: `step-correlation-missing` is `state-error` — "a step correlation is missing or of
        the wrong kind" — and the assignment is "fixed by kind, never per-function taste"; journaled."""
        start_session_log(log_store)
        append_resolution(log_store, workflow_instance_id=None)
        resolver = _build_resolver(log_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "state-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "step-correlation-missing"
        journaled = read_entries(workspace_dir, ORCHESTRATOR_SESSION)
        assert len(journaled) == 3
        assert journaled[-1]["report"]["outcome"]["status"] == "state-error"

    def test_reports_configuration_error_when_the_model_catalog_is_empty(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Rule 1: an empty catalog reaching runtime is "configuration invalid at use time" —
        `configuration-error`, journaled; invariant 4's load-time rejection is not enforced by the
        `model-profiles.conf` contract, so the branch stays reachable."""
        start_session_log(log_store)
        append_resolution(log_store)
        resolver = _build_resolver(log_store, profiles=build_profiles())

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "configuration-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "empty-model-catalog"
        assert len(read_entries(workspace_dir, ORCHESTRATOR_SESSION)) == 3

    def test_refuses_an_unregistered_session_with_a_report_it_cannot_journal(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Precondition (E) / rule 4: `session-unregistered` returns a report with no log to journal to."""
        resolver = _build_resolver(log_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "inquiry-error"
        assert report.outcome.error is not None
        assert report.outcome.error.code == "session-unregistered"
        assert read_entries(workspace_dir, ORCHESTRATOR_SESSION) == ()

    def test_reads_the_step_capabilities_from_the_workflow_configuration(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 1 / Interface: weights come from the workflow configuration, never from the caller."""
        start_session_log(log_store)
        append_resolution(log_store, weights=build_capabilities(coding=10.0))
        resolver = _build_resolver(
            log_store,
            step=build_step(weights=build_capabilities(deep_reasoning=9.0)),
            profiles=build_profiles(
                ("deep-model", 3, build_capabilities(deep_reasoning=9.0, coding=0.0)),
                ("coding-model", 1, build_capabilities(deep_reasoning=0.0, coding=9.0)),
            ),
        )

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.profile is not None
        assert report.profile.slug == "deep-model"
        assert report.profile.score == 81.0

    def test_scores_models_as_the_weighted_capability_sum(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 3 (worked example): weight 9 against scores 9 and 7 gives 81 vs 63 — A wins on capability."""
        start_session_log(log_store)
        append_resolution(log_store)
        resolver = _build_resolver(
            log_store,
            step=build_step(weights=build_capabilities(deep_reasoning=9.0)),
            profiles=build_profiles(("model-a", 3, _DEEP_NINE), ("model-b", 1, _DEEP_SEVEN)),
        )

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.profile is not None
        assert report.profile.slug == "model-a"
        assert report.profile.score == 81.0
        assert report.profile.cost_rank == 3

    def test_narrows_the_score_spread_at_a_lower_weight(self, log_store: SessionLogStore) -> None:
        """Invariant 3 (worked example): at weight 3 the same pair scores 27 vs 21 — the band compresses."""
        start_session_log(log_store)
        append_resolution(log_store)
        resolver = _build_resolver(
            log_store,
            step=build_step(weights=build_capabilities(deep_reasoning=3.0)),
            profiles=build_profiles(("model-a", 3, _DEEP_NINE), ("model-b", 1, _DEEP_SEVEN)),
        )

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.profile is not None
        assert report.profile.score == 27.0
        assert report.profile.score - 21.0 < 81.0 - 63.0

    def test_breaks_a_score_tie_toward_the_lower_cost_rank(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 3: equal scores break toward the lower `costRank` — cost sensitivity emerges structurally."""
        start_session_log(log_store)
        append_resolution(log_store)
        resolver = _build_resolver(
            log_store,
            step=build_step(weights=build_capabilities(deep_reasoning=3.0)),
            profiles=build_profiles(("expensive", 5, _DEEP_NINE), ("cheap", 1, _DEEP_NINE)),
        )

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.profile is not None
        assert report.profile.slug == "cheap"
        assert report.profile.cost_rank == 1

    def test_breaks_a_cost_rank_tie_toward_the_lowest_slug(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 3: equal score and equal `costRank` break to the lexicographically lowest slug."""
        start_session_log(log_store)
        append_resolution(log_store)
        resolver = _build_resolver(
            log_store,
            profiles=build_profiles(("zulu", 2, _DEEP_NINE), ("alpha", 2, _DEEP_NINE)),
        )

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.profile is not None
        assert report.profile.slug == "alpha"

    def test_rejects_an_unroutable_all_zero_weight_map_at_configuration_load(
        self, config_loader, framework_root: Path
    ) -> None:
        """Invariant 4 (C precondition): an all-zero weight map is unroutable and dies at load, not at runtime."""
        write_unroutable_workflow(framework_root)

        with pytest.raises(ConfigurationError) as raised:
            config_loader.load_workflow_catalog(framework_root)

        assert raised.value.code == "missing-capability-weight"

    def test_resolves_the_identical_profile_on_every_call_for_the_same_step(
        self, log_store: SessionLogStore
    ) -> None:
        """Invariant 5: the profile is a pure function of static configuration and the deduced step."""
        start_session_log(log_store)
        append_resolution(log_store)
        resolver = _build_resolver(log_store)

        first = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)
        second = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert first.profile == second.profile
        assert first.to_dict()["profile"] == second.to_dict()["profile"]

    def test_reports_configuration_error_when_the_in_flight_step_left_the_catalog(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Rule 1: configuration invalid at use time is `configuration-error`, journaled."""
        start_session_log(log_store)
        append_resolution(log_store, step_slug="retired-step")
        resolver = _build_resolver(log_store)

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "configuration-error"
        assert report.outcome.error is not None
        journaled = read_entries(workspace_dir, ORCHESTRATOR_SESSION)
        assert len(journaled) == 3
        assert journaled[-1]["report"]["outcome"]["status"] == "configuration-error"

    def test_journals_one_entry_carrying_the_resolved_profile(
        self, log_store: SessionLogStore, workspace_dir: Path
    ) -> None:
        """Postcondition / Out: 1 invocation = 1 entry, correlated to the deduced step's instance."""
        start_session_log(log_store)
        append_resolution(log_store)
        resolver = _build_resolver(log_store, clock=SequenceClock("2026-08-17T15:00:00Z"))

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.outcome.status == "resolved"
        assert report.context.function == "resolve-step-model"
        assert report.context.workflow_instance_id == INSTANCE_ID
        assert report.profile is not None
        assert report.profile.reason
        journaled = read_entries(workspace_dir, ORCHESTRATOR_SESSION)
        assert len(journaled) == 3
        entry = LogEntry.from_dict(journaled[-1])
        assert entry.timestamp == "2026-08-17T15:00:00Z"
        assert entry.report.to_dict() == report.to_dict()

    def test_deduces_the_step_from_the_session_logs_without_being_told(
        self, log_store: SessionLogStore
    ) -> None:
        """Interface: the harness deduces WHICH step from its own logs — the agent asks, never describes."""
        start_session_log(log_store)
        append_resolution(log_store, step_slug=STEP_SLUG)
        resolver = _build_resolver(
            log_store,
            step=build_step(slug=STEP_SLUG, weights=build_capabilities(coding=5.0)),
            profiles=build_profiles(("coder", 1, build_capabilities(coding=8.0))),
        )

        report = resolver.resolve_step_model(ORCHESTRATOR_SESSION, None)

        assert report.profile is not None
        assert report.profile.score == 40.0


class TestModelProfileReport:
    """Cover the report the model resolution service returns."""

    def test_renders_the_not_applicable_report_without_a_profile_property(self) -> None:
        """Rule 2: `not-applicable` carries no function-specific payload."""
        report = ModelProfileReport(
            context=Context(function="resolve-step-model", session_id=ORCHESTRATOR_SESSION),
            outcome=Outcome(status="not-applicable"),
        )

        assert report.to_dict() == {
            "context": {
                "function": "resolve-step-model",
                "sessionId": ORCHESTRATOR_SESSION,
                "parentSessionId": None,
                "workflowInstanceId": None,
            },
            "outcome": {"status": "not-applicable"},
        }


class TestModelProfileBinding:
    """Cover the canonical model profile the report carries."""

    def test_renders_the_canonical_profile_contract_object(self) -> None:
        """Out: the canonical profile `{slug, score, costRank, reason}` — never a host model id."""
        binding = ModelProfileBinding(
            slug="claude-sonnet-4",
            score=144.0,
            cost_rank=2,
            reason="highest weighted capability score",
        )

        assert binding.to_dict() == {
            "slug": "claude-sonnet-4",
            "score": 144.0,
            "costRank": 2,
            "reason": "highest weighted capability score",
        }
