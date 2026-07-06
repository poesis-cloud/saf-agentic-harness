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

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config import Workflow
from config import FrameworkConfig
from mappers import ArtifactMapper, Workspace
from mappers import LogMapper
from services import ModelRouter, OrchestrationService


def _engine() -> OrchestrationService:
    ws = Workspace.detect()
    cfg = FrameworkConfig.detect(ws)
    return OrchestrationService(ws, cfg.workflows, ArtifactMapper(ws), ModelRouter(cfg.model_profiles), LogMapper(ws))


def _sample_workflow() -> Workflow:
    """A four-step linear workflow: author -> challenge -> ★ gate -> commit, sequenced by `after`.

    Actors are picked from the real ACL so dispatch validation exercises real agents; steps
    carry weighted capabilities so the router resolves from the model catalog.
    """
    cfg = FrameworkConfig.detect(Workspace.detect())
    policy = cfg.access_control_list
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
                        "artifacts": ["artifact"],
                        "capabilities": caps,
                    },
                    {
                        "id": "review",
                        "actor": f"@{reviewer}",
                        "artifacts": ["review"],
                        "capabilities": caps,
                        "conditions": [
                            {"id": "after_draft", "kind": "precondition", "type": "after", "step_id": "draft"},
                        ],
                    },
                    {
                        "id": "approve",
                        "actor": f"@{gatekeeper}",
                        "capabilities": caps,
                        "conditions": [{"id": "after_review", "kind": "precondition", "type": "after", "step_id": "review"}],
                    },
                    {
                        "id": "land",
                        "actor": f"@{gatekeeper}",
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
    assert engine.router.validate_dispatch(action["model"]) is None


def test_second_step_routes_on_default() -> None:
    engine = _engine()
    wf = _sample_workflow()
    action = engine.next_action(wf, completed={"draft"}, unit="u-1")
    assert action["action"] == "dispatch"
    assert action["step"] == "review"
    # routes on the actor-derived role default; dispatch stays on-policy.
    actor = action["actor"].lstrip("@")
    assert engine.router.validate_dispatch(action["model"]) is None
    assert action["routing"]["model"] == action["model"]
    assert action["routing"]["score"] > 0


def test_catalog_propose_without_workflow() -> None:
    # no --workflow: the engine PROPOSES the eligible next natural workflow(s) from the catalog.
    action = _engine().orchestrate(run=None)
    assert action["action"] == "propose"
    assert action["completed"] == []
    # entry points (no advisory predecessors) are eligible from a cold start.
    assert "epic-lean-business-case" in action["eligible"]
    assert "iteration-planning" in action["eligible"]
    # advisory-sequenced workflows are not (their predecessors are not complete)...
    assert "verification" not in action["eligible"]


def test_advisory_sequence_never_constrains_direct_drive() -> None:
    # ...but the user may still drive them directly: assent, not the DAG, starts a workflow.
    action = _engine().orchestrate("verification", unit="u-1")
    assert action["action"] == "dispatch"


def test_blocked_when_predecessor_unmet() -> None:
    # Defensive branch: every remaining step waits on an unmet predecessor (here a dangling id),
    # so nothing is eligible while work remains -> the engine halts (blocked) rather than looping.
    policy = FrameworkConfig.detect(Workspace.detect()).access_control_list
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


def test_real_workflow_drives_from_id() -> None:
    """Smoke: a real root workflow resolves to a concrete first action from its id alone (no unit
    artifacts on disk -> the cursor is empty -> the first authored step dispatches or halts at a gate)."""
    engine = _engine()
    wf = next(iter(FrameworkConfig.detect(Workspace.detect()).workflows.all()), None)
    assert wf is not None, "expected at least one workflow"
    action = engine.orchestrate(str(wf.id), unit=None)
    assert action["action"] in {"dispatch", "propose", "halt", "done"}
    if action["action"] == "dispatch":
        # a real dispatch must carry a resolved, on-policy model.
        assert action["model"]
        assert engine.router.validate_dispatch(action["model"]) is None
