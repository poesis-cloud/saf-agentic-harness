"""Unit tests for the adapter's session-id derivation — host-observed, never model-authored."""

from __future__ import annotations

import re

from session_identity import (
    derive_step_session_id,
    derive_turn_session_id,
    sanitize_identifier,
)

_CONTRACT_SESSION_ID = re.compile(r"^[a-z0-9-]+$")


class TestSessionIdentity:
    """Adapter spec — Session identity binding."""

    def test_sanitizes_by_lowercasing_and_mapping_unsafe_characters(self) -> None:
        """Adapter spec — Session identity binding: sanitization lowercases; any
        character outside `[a-z0-9-]` maps to `-` (the id becomes a log filename).
        """
        assert sanitize_identifier("6A3F_Chat.Session/GUID") == "6a3f-chat-session-guid"
        assert sanitize_identifier("../escape") == "---escape"

    def test_derives_the_turn_session_id_from_the_envelope_timestamp_alone(self) -> None:
        """Adapter spec H0 / Session identity binding: an orchestrator turn session is
        `<sanitized session_id>-t<sanitized event timestamp>`, computed purely from the
        stdin envelope — zero reads of any kind.
        """
        assert (
            derive_turn_session_id("chat-session-guid", "2026-07-11T14:32:07.000Z")
            == "chat-session-guid-t2026-07-11t14-32-07-000z"
        )

    def test_derives_the_same_turn_session_id_for_a_re_delivered_firing(self) -> None:
        """Adapter spec H0, invariant 2: a host re-delivery of the same firing reproduces
        the identical id — start-session's own idempotency absorbs it.
        """
        first = derive_turn_session_id("chat-session-guid", "2026-07-11T14:32:07.000Z")
        second = derive_turn_session_id("chat-session-guid", "2026-07-11T14:32:07.000Z")

        assert first == second

    def test_derives_distinct_turn_session_ids_for_distinct_turns(self) -> None:
        """Adapter spec H0, invariant 2: 1 firing = 1 agent session = 1 log — a genuinely
        new turn never collides with an existing one.
        """
        first = derive_turn_session_id("chat-session-guid", "2026-07-11T14:32:07.000Z")
        second = derive_turn_session_id("chat-session-guid", "2026-07-11T14:35:00.000Z")

        assert first != second

    def test_derives_the_step_session_id_from_the_subagent_invocation_id(self) -> None:
        """Adapter spec H1 / Session identity binding: a step session id is the sanitized
        `agent_id` — the subagent INVOCATION id, unique per dispatch, so 1 step = 1
        session holds.
        """
        assert derive_step_session_id("SubAgent_Invocation-ID") == "subagent-invocation-id"

    def test_derives_ids_the_harness_context_contract_accepts(self) -> None:
        """Adapter spec — Session identity binding: ids are normalized to the safe slug
        form `^[a-z0-9-]+$` the harness `context` contract requires.
        """
        turn = derive_turn_session_id("6A3F…-chat", "2026-07-11T14:32:07.000Z")
        step = derive_step_session_id("call_ABC/123")

        assert _CONTRACT_SESSION_ID.match(turn)
        assert _CONTRACT_SESSION_ID.match(step)
