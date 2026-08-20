"""Tests for the shared inquiry envelope."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass

import pytest

from commands.inquiry import Inquiry


@dataclass(frozen=True)
class _BareInquiry(Inquiry):
    """A function inquiry adding no field of its own."""


class TestInquiry:
    """The abstract base every function inquiry extends."""

    def test_carries_the_session_attribution_pair_every_inquiry_shares(self) -> None:
        """Spec (The harness functions): every session-bound function's `in` carries the
        session attribution fields directly — `sessionId` and nullable `parentSessionId`
        — and that pair IS the shared inquiry envelope every input contract roots.
        """
        inquiry = _BareInquiry(session_id="s1", parent_session_id="p1")

        assert inquiry.session_id == "s1"
        assert inquiry.parent_session_id == "p1"

    def test_admits_a_null_parent_for_a_top_level_session(self) -> None:
        """Spec (contracts/inquiry.schema.json): `parentSessionId` is nullable — null for
        a top-level session, the parent's id for a session another session opened.
        """
        assert _BareInquiry(session_id="s1", parent_session_id=None).parent_session_id is None

    def test_is_immutable_like_every_boundary_dataclass(self) -> None:
        """Spec (Python conventions): immutability by default — dataclasses are
        `frozen=True` and expose public typed attributes, never mutable attribute bags.
        """
        inquiry = _BareInquiry(session_id="s1", parent_session_id=None)

        with pytest.raises(FrozenInstanceError):
            setattr(inquiry, "session_id", "s2")
