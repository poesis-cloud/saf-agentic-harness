"""One step instance's step ref: the local-only Git ref carrying its validated commits."""

from __future__ import annotations

from dataclasses import dataclass

STEP_REF_NAMESPACE = "refs/harness/steps/"


@dataclass(frozen=True)
class StepRef:
    """Name one step instance's ref and the commit it points at.

    Spec (Terminology, Step ref): `refs/harness/steps/<workflowInstanceId>/<stepSlug>/
    <workerSessionId>` — keyed by the step instance itself, so every leaf is a
    worker session id and no two step refs conflict in Git's ref namespace.
    """

    workflow_instance_id: str
    step_slug: str
    session_id: str
    tip: str

    @property
    def name(self) -> str:
        """Render the full ref name."""
        return build_step_ref_name(
            self.workflow_instance_id, self.step_slug, self.session_id
        )

    @classmethod
    def parse_step_ref(cls, name: str, tip: str) -> StepRef | None:
        """Parse a full ref name under the step namespace, or none if malformed."""
        if not name.startswith(STEP_REF_NAMESPACE):
            return None
        parts = name[len(STEP_REF_NAMESPACE) :].split("/")
        if len(parts) != 3 or not all(parts):
            return None
        return cls(
            workflow_instance_id=parts[0],
            step_slug=parts[1],
            session_id=parts[2],
            tip=tip,
        )


def build_step_ref_name(
    workflow_instance_id: str, step_slug: str, session_id: str
) -> str:
    """Render the step ref name of one step instance."""
    return f"{STEP_REF_NAMESPACE}{workflow_instance_id}/{step_slug}/{session_id}"


__all__ = ["STEP_REF_NAMESPACE", "StepRef", "build_step_ref_name"]
