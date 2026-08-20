"""Session-id derivation from host-observed event data — never model-authored."""

from __future__ import annotations

import re

_UNSAFE_CHARACTER = re.compile(r"[^a-z0-9-]")
_TURN_MARKER = "-t"


def sanitize_identifier(raw_identifier: str) -> str:
    """Normalize a host id to the safe slug form the harness context contract requires.

    Spec (adapter, Session identity binding): lowercase; any character outside
    `[a-z0-9-]` maps to `-` — the id becomes a log filename, so a raw host id is a
    path-traversal vector.
    """
    return _UNSAFE_CHARACTER.sub("-", raw_identifier.lower())


def derive_turn_session_id(raw_host_session_id: str, timestamp: str) -> str:
    """Derive an orchestrator turn's session id from the stdin envelope alone.

    Spec (adapter, H0): `<sanitized session_id>-t<sanitized event timestamp>` — computed
    purely from host-observed data, zero reads of any kind, so a re-delivered firing
    reproduces the identical id.
    """
    return (
        f"{sanitize_identifier(raw_host_session_id)}"
        f"{_TURN_MARKER}{sanitize_identifier(timestamp)}"
    )


def derive_step_session_id(raw_agent_id: str) -> str:
    """Derive a step session's id from the subagent invocation id.

    Spec (adapter, H1, invariant 1): `agent_id` is unique per dispatch, so 1 step = 1
    session holds even across retries of the same step.
    """
    return sanitize_identifier(raw_agent_id)


__all__ = [
    "derive_step_session_id",
    "derive_turn_session_id",
    "sanitize_identifier",
]
