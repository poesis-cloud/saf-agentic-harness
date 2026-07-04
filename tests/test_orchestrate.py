"""Orchestrate engine TEST — the `drive` plane resolves dispatch | halt | done deterministically.

DESIGN-TIME test for ``OrchestrationService``: the pure sequencing core (``next_action`` over a
workflow + a set of completed step ids) returns the first eligible step as a policy-valid
``dispatch``, halts at a ``gate``, halts when a predecessor is unmet, and reports ``done`` once every
step is complete — and a smoke pass drives a real root workflow from id alone. Actors are loaded
from ``conf/access-control-list.conf.yaml`` so the synthetic workflow stays methodology-agnostic. Run via pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Workflow
from config import FrameworkConfig
from mappers import ArtifactMapper, Workspace
from services import AuthorizationPolicy, ModelRouter, OrchestrationService


def _engine() -> OrchestrationService:
    ws = Workspace.detect()
    cfg = FrameworkConfig.detect(ws)
    return OrchestrationService(ws, cfg.workflows, ArtifactMapper(ws), ModelRouter(cfg.model_profiles))


def _sample_workflow() -> Workflow:
    """A four-step linear workflow: author -> challenge -> ★ gate -> commit, sequenced by `after`.

    Actors are picked from the real ACL so dispatch validation exercises real agents; steps
    carry weighted capabilities so the router resolves from the model catalog.
    """
    cfg = FrameworkConfig.detect(Workspace.detect())
    policy = AuthorizationPolicy(cfg.access_control_list)
    agents = sorted(policy.agents().keys())
    assert len(agents) >= 3, "ACL must define at least three agents for the synthetic workflow"

    author = agents[0]
    reviewer = agents[1]
    gatekeeper = agents[-1]
    caps = {"deep-reasoning": 7, "writing-quality": 8}

    return Workflow(
        {
            "workflow": {
                "id": "sample-drive",
                "rank": "root",
                "steps": [
                    {
                        "id": "draft",
                        "actor": f"@{author}",
                        "kind": "author",
                        "output": "artifact",
                        "capabilities": caps,
                    },
                    {
                        "id": "review",
                        "actor": f"@{reviewer}",
                        "kind": "challenge",
                        "output": "review",
                        "capabilities": caps,
                        "conditions": [
                            {"id": "after_draft", "kind": "precondition", "type": "after", "step_id": "draft"},
                        ],
                    },
                    {
                        "id": "approve",
                        "actor": f"@{gatekeeper}",
                        "kind": "gate",
                        "conditions": [{"id": "after_review", "kind": "precondition", "type": "after", "step_id": "review"}],
                    },
                    {
                        "id": "land",
                        "actor": f"@{gatekeeper}",
                        "kind": "commit",
                        "conditions": [{"id": "after_approve", "kind": "precondition", "type": "after", "step_id": "approve"}],
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
    assert action["unit"] == "u-1"
    # the model resolved and clears the role-default floor (validate_dispatch returned no error).
    assert action["model"]
    actor = action["actor"].lstrip("@")
    assert engine.router.validate_dispatch(actor, action["model"]) is None


def test_second_step_routes_on_default() -> None:
    engine = _engine()
    wf = _sample_workflow()
    action = engine.next_action(wf, completed={"draft"}, unit="u-1")
    assert action["action"] == "dispatch"
    assert action["step"] == "review"
    # routes on the actor-derived role default; dispatch stays on-policy.
    actor = action["actor"].lstrip("@")
    assert engine.router.validate_dispatch(actor, action["model"]) is None
    assert action["routing"]["model"] == action["model"]
    assert action["routing"]["score"] > 0


def test_gate_halts() -> None:
    action = _engine().next_action(_sample_workflow(), completed={"draft", "review"}, unit="u-1")
    assert action["action"] == "halt"
    assert action["reason"] == "gate"
    assert action["step"] == "approve"


def test_blocked_when_predecessor_unmet() -> None:
    # Defensive branch: every remaining step waits on an unmet predecessor (here a dangling id),
    # so nothing is eligible while work remains -> the engine halts (blocked) rather than looping.
    policy = AuthorizationPolicy(FrameworkConfig.detect(Workspace.detect()).access_control_list)
    agents = sorted(policy.agents().keys())
    author = agents[0] if agents else "developer"
    wf = Workflow(
        {
            "workflow": {
                "id": "dangling-drive",
                "rank": "root",
                "steps": [
                    {
                        "id": "only",
                        "actor": f"@{author}",
                        "kind": "author",
                        "conditions": [{"id": "after_missing", "kind": "precondition", "type": "after", "step_id": "missing"}],
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
    wf = next((w for w in FrameworkConfig.detect(Workspace.detect()).workflows.all() if w.is_root), None)
    assert wf is not None, "expected at least one root workflow"
    action = engine.orchestrate(str(wf.id), unit=None)
    assert action["action"] in {"dispatch", "halt", "done"}
    if action["action"] == "dispatch":
        # a real dispatch must carry a resolved, on-policy model.
        assert action["model"]
        assert engine.router.validate_dispatch(str(action.get("actor", "")).lstrip("@"), action["model"]) is None
