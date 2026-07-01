"""Orchestrate engine TEST — the `drive` plane resolves dispatch | halt | done deterministically.

DESIGN-TIME test for ``OrchestrationService``: the pure sequencing core (``next_action`` over a
workflow + a set of completed step ids) returns the first eligible step as a policy-valid
``dispatch``, halts at a ``gate``, halts when a predecessor is unmet, and reports ``done`` once every
step is complete — and a smoke pass drives a real root workflow from id alone. Run via ``make verify``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Workflow
from persistence import ArtifactRepository, Workspace, WorkflowRepository
from services import ModelRouter, OrchestrationService


def _engine() -> OrchestrationService:
    ws = Workspace.detect()
    return OrchestrationService(ws, WorkflowRepository(ws), ArtifactRepository(ws), ModelRouter(ws))


def _sample_workflow() -> Workflow:
    """A four-step linear workflow: author -> challenge -> ★ gate -> commit, with routing metadata."""
    return Workflow(
        {
            "workflow": {
                "id": "sample-drive",
                "rank": "root",
                "steps": [
                    {
                        "id": "draft",
                        "actor": "@product-manager",
                        "kind": "author",
                        "role": "product-manager",
                        "output": "feature",
                        "conditions": [{"kind": "produces", "type": "output", "expression": "ref", "value": "feature"}],
                    },
                    {
                        "id": "review",
                        "actor": "@security-expert",
                        "kind": "challenge",
                        "role": "security-expert",
                        "risk": "critical",
                        "tags": ["deep-reasoning"],
                        "output": "security-review",
                        "conditions": [
                            {"kind": "after", "type": "after", "expression": "ref", "value": "draft"},
                            {"kind": "produces", "type": "output", "expression": "ref", "value": "security-review"},
                        ],
                    },
                    {
                        "id": "approve",
                        "actor": "@release-train-engineer",
                        "kind": "gate",
                        "role": "release-train-engineer",
                        "conditions": [{"kind": "after", "type": "after", "expression": "ref", "value": "review"}],
                    },
                    {
                        "id": "land",
                        "actor": "@release-train-engineer",
                        "kind": "commit",
                        "role": "release-train-engineer",
                        "conditions": [{"kind": "after", "type": "after", "expression": "ref", "value": "approve"}],
                    },
                ],
            }
        },
        path=None,
    )


def test_first_step_dispatches_policy_valid() -> None:
    engine = _engine()
    wf = _sample_workflow()
    action = engine.next_action(wf, completed=set(), unit="u-1", run="r-1")
    assert action["action"] == "dispatch"
    assert action["step"] == "draft"
    assert action["actor"] == "@product-manager"
    assert action["unit"] == "u-1"
    # the model resolved and clears the role-default floor (validate_dispatch returned no error).
    assert action["model"]
    assert engine.router.validate_dispatch("product-manager", action["model"]) is None


def test_risk_raises_tier_and_routes() -> None:
    engine = _engine()
    wf = _sample_workflow()
    action = engine.next_action(wf, completed={"draft"}, unit="u-1")
    assert action["action"] == "dispatch"
    assert action["step"] == "review"
    # critical risk + deep-reasoning tag pulls the strongest model; dispatch stays on-policy.
    assert engine.router.validate_dispatch("security-expert", action["model"]) is None
    assert action["routing"]["tier"] == engine.router.models()[action["model"]]["tier"]


def test_gate_halts() -> None:
    action = _engine().next_action(_sample_workflow(), completed={"draft", "review"}, unit="u-1")
    assert action["action"] == "halt"
    assert action["reason"] == "gate"
    assert action["step"] == "approve"


def test_blocked_when_predecessor_unmet() -> None:
    # Defensive branch: every remaining step waits on an unmet predecessor (here a dangling id),
    # so nothing is eligible while work remains -> the engine halts (blocked) rather than looping.
    wf = Workflow(
        {
            "workflow": {
                "id": "dangling-drive",
                "rank": "root",
                "steps": [
                    {
                        "id": "only",
                        "actor": "@developer",
                        "kind": "author",
                        "role": "developer",
                        "conditions": [{"kind": "after", "type": "after", "expression": "ref", "value": "missing"}],
                    }
                ],
            }
        },
        path=None,
    )
    action = _engine().next_action(wf, completed=set(), unit="u-1")
    assert action["action"] == "halt"
    assert action["reason"] == "blocked"
    assert "missing" in action["unmet_predecessors"]


def test_done_when_all_complete() -> None:
    completed = {"draft", "review", "approve", "land"}
    action = _engine().next_action(_sample_workflow(), completed=completed, unit="u-1")
    assert action["action"] == "done"


def test_unknown_workflow_is_error() -> None:
    action = _engine().orchestrate("no-such-workflow", unit="u-1")
    assert action["action"] == "error"


def test_real_root_workflow_drives_from_id() -> None:
    """Smoke: a real root workflow resolves to a concrete first action from its id alone (no unit
    artifacts on disk -> the cursor is empty -> the first authored step dispatches or halts at a gate)."""
    engine = _engine()
    wf = next((w for w in WorkflowRepository(Workspace.detect()).all() if w.is_root), None)
    assert wf is not None, "expected at least one root workflow"
    action = engine.orchestrate(str(wf.id), unit=None)
    assert action["action"] in {"dispatch", "halt", "done"}
    if action["action"] == "dispatch":
        # a real dispatch must carry a resolved, on-policy model.
        assert action["model"]
        assert engine.router.validate_dispatch(str(action.get("actor", "")).lstrip("@"), action["model"]) is None
