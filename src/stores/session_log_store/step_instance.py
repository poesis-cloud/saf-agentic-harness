"""One step instance as the logs record it: the condition language's `steps` element."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StepInstance:
    """Carry one step instance's identity, its outcome, and its delivered artifact.

    Spec (function 5, invariant 2, Step instance fields): `outcome` is `pass`
    when its function-10 outcome passed; `fail` when function 10 concluded it
    otherwise, by a `fail` or an error outcome; and `in-flight` while no
    function-10 outcome concludes it — a crashed or abandoned step instance stays
    `in-flight`. `artifact_kind` / `artifact_path` come from that outcome's
    `artifactDelivery` when it names an `artifactPath`, else both are none.
    """

    step_slug: str
    session_id: str
    workflow_instance_id: str
    actor: str
    outcome: str
    registered_at: str
    artifact_kind: str | None = None
    artifact_path: str | None = None


__all__ = ["StepInstance"]
