"""Unit tests for `SessionTracker` — the adapter's own private stack per host session id."""

from __future__ import annotations

from pathlib import Path

import pytest

from session_tracker import SessionTracker


@pytest.fixture
def tracker(tmp_path: Path) -> SessionTracker:
    return SessionTracker(tmp_path / "sessions.json")


class TestSessionTracker:
    """Adapter spec — Session identity binding + Session correlation scenarios."""

    def test_resolves_no_session_for_a_host_id_it_never_saw(
        self, tracker: SessionTracker
    ) -> None:
        """Adapter spec — Session correlation scenario 8: a firing for a `session_id`
        never seen before (foreign agent) resolves to None — nothing was registered to
        misattribute to.
        """
        assert tracker.resolve_current("chat-session-guid") is None

    def test_resets_the_stack_to_the_new_turn_session(
        self, tracker: SessionTracker
    ) -> None:
        """Adapter spec H0 / correlation scenario 3: H0 resets the stack for this
        `session_id` fresh and pushes the orchestrator's new turn session as its base.
        """
        tracker.reset_current("chat-session-guid", "chat-session-guid-t1")

        assert tracker.resolve_current("chat-session-guid") == "chat-session-guid-t1"
        assert tracker.resolve_base("chat-session-guid") == "chat-session-guid-t1"

    def test_resets_discard_whatever_a_prior_turn_left(
        self, tracker: SessionTracker
    ) -> None:
        """Adapter spec — correlation scenario 4: `Stop` never ran and the next activity
        IS a framework agent — H0 resets unconditionally, so the stale entry cannot
        survive to be read.
        """
        tracker.reset_current("chat-session-guid", "chat-session-guid-t1")
        tracker.push_current("chat-session-guid", "orphaned-step")

        tracker.reset_current("chat-session-guid", "chat-session-guid-t2")

        assert tracker.resolve_current("chat-session-guid") == "chat-session-guid-t2"
        assert tracker.resolve_parent("chat-session-guid") is None

    def test_pushes_the_step_session_over_its_dispatching_session(
        self, tracker: SessionTracker
    ) -> None:
        """Adapter spec H1 / correlation scenario 2: H1 pushes the new step session on
        top; the previous top (the dispatching turn session) stays below as its parent.
        """
        tracker.reset_current("chat-session-guid", "chat-session-guid-t1")
        tracker.push_current("chat-session-guid", "subagent-invocation-id")

        assert tracker.resolve_current("chat-session-guid") == "subagent-invocation-id"
        assert tracker.resolve_parent("chat-session-guid") == "chat-session-guid-t1"

    def test_pops_the_step_session_back_to_the_dispatching_session(
        self, tracker: SessionTracker
    ) -> None:
        """Adapter spec H7 / correlation scenario 2: `SubagentStop` pops the step session
        back off — without it the orchestrator's next mediated call would misattribute to
        the already-ended step session.
        """
        tracker.reset_current("chat-session-guid", "chat-session-guid-t1")
        tracker.push_current("chat-session-guid", "subagent-invocation-id")

        tracker.pop_current("chat-session-guid")

        assert tracker.resolve_current("chat-session-guid") == "chat-session-guid-t1"

    def test_pops_an_empty_stack_without_failing(self, tracker: SessionTracker) -> None:
        """Adapter spec H7, invariant 2: a duplicate `SubagentStop` delivery changes
        nothing — closure is idempotent, so the pop must be too.
        """
        tracker.pop_current("chat-session-guid")

        assert tracker.resolve_current("chat-session-guid") is None

    def test_clears_the_whole_stack_on_the_turn_ending(
        self, tracker: SessionTracker
    ) -> None:
        """Adapter spec H7 (`Stop`): the turn is over — an emptied tracker makes any later
        firing in this conversation under a non-framework agent resolve to None, the
        correct C7 pass-through, instead of a stale framework session.
        """
        tracker.reset_current("chat-session-guid", "chat-session-guid-t1")
        tracker.push_current("chat-session-guid", "subagent-invocation-id")

        tracker.clear_current("chat-session-guid")

        assert tracker.resolve_current("chat-session-guid") is None
        assert tracker.resolve_base("chat-session-guid") is None

    def test_keeps_two_conversations_isolated(self, tracker: SessionTracker) -> None:
        """Adapter spec — correlation scenario 1: two concurrent conversations each
        resolve only their own; the tracker is keyed by the host's own `session_id`,
        distinct and host-assigned per conversation.
        """
        tracker.reset_current("conversation-a", "conversation-a-t1")
        tracker.reset_current("conversation-b", "conversation-b-t1")
        tracker.push_current("conversation-b", "step-of-b")

        assert tracker.resolve_current("conversation-a") == "conversation-a-t1"
        assert tracker.resolve_current("conversation-b") == "step-of-b"

    def test_resolves_the_stack_base_while_a_step_is_in_flight(
        self, tracker: SessionTracker
    ) -> None:
        """Adapter spec H6: the step-ended hook resolves the stack BASE (the
        orchestrator's turn session), not the raw top — correct under either
        `SubagentStop`/`PostToolUse` ordering.
        """
        tracker.reset_current("chat-session-guid", "chat-session-guid-t1")
        tracker.push_current("chat-session-guid", "subagent-invocation-id")

        assert tracker.resolve_base("chat-session-guid") == "chat-session-guid-t1"

    def test_keeps_a_stale_session_when_neither_stop_nor_a_new_turn_ran(
        self, tracker: SessionTracker
    ) -> None:
        """Adapter spec — correlation scenario 5, the bounded residual gap: nothing resets
        a stack that only H0 clears/resets, so a stale session stays resolvable until this
        conversation's next framework H0. Asserted deliberately: the gap is a declared,
        bounded exposure, not an accident.
        """
        tracker.reset_current("chat-session-guid", "chat-session-guid-t1")

        assert tracker.resolve_current("chat-session-guid") == "chat-session-guid-t1"

        tracker.reset_current("chat-session-guid", "chat-session-guid-t2")

        assert tracker.resolve_current("chat-session-guid") == "chat-session-guid-t2"

    def test_shares_its_record_across_hook_processes(self, tmp_path: Path) -> None:
        """Adapter spec — Session identity binding: every hook firing is its own process,
        so the tracker's record is the adapter's OWN small PERSISTENT bookkeeping (plain
        file I/O, no harness dependency).
        """
        store_path = tmp_path / "sessions.json"
        SessionTracker(store_path).reset_current("chat-session-guid", "chat-session-guid-t1")

        assert SessionTracker(store_path).resolve_current("chat-session-guid") == (
            "chat-session-guid-t1"
        )
