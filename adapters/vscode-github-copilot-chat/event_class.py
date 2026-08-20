"""The closed set of classes one host firing can land in."""

from __future__ import annotations

from enum import Enum, unique


@unique
class EventClass(Enum):
    """Name the boundary a host firing binds to.

    Spec (adapter, I14): NINE classes — the seven harness boundaries plus the two host
    events that reach no harness function. Lifecycle participles avoid the step/session
    clash: `STEP_STARTING` is the dispatch about to open the step (it can deny),
    `STEP_STARTED` is the step session having opened (it can only inject), `STEP_ENDED`
    is the dispatch return (THE evaluation point). `MEDIATED_ATTRIBUTION` (H4) invokes no
    function at all; `PASS_THROUGH` is every other firing.
    """

    SESSION_STARTED = "session-started"
    STEP_STARTING = "step-starting"
    STEP_STARTED = "step-started"
    WRITE_STARTING = "write-starting"
    WRITE_ENDED = "write-ended"
    STEP_ENDED = "step-ended"
    SESSION_ENDED = "session-ended"
    MEDIATED_ATTRIBUTION = "mediated-attribution"
    PASS_THROUGH = "pass-through"


__all__ = ["EventClass"]
