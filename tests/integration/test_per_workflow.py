"""Per-workflow drive TEST — every workflow is drivable end-to-end and routes on-policy.

DESIGN-TIME framework test parameterized over EVERY Poesis-owned ``workflow.yaml``. For each one it
simulates the harness ``drive`` loop against the pure sequencing core (``OrchestrationService.
next_action``): from an empty cursor it repeatedly resolves the next action, marks the dispatched (or
passed ★ gate) step complete, and continues until ``done`` — asserting the graph never deadlocks
(``blocked``) and that every ``dispatch`` carries a resolved, on-policy model. It also asserts the
mediation doctrine is gone: no ``facilitate`` step survives (the harness is the sole implicit
mediator). Run via ``make verify``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

from config import FrameworkConfig
from mappers import ArtifactMapper, LogMapper, Workspace
from services import ModelRouter, OrchestrationService


def _workspace() -> Workspace:
    return Workspace.detect()


def _engine(ws: Workspace) -> OrchestrationService:
    cfg = FrameworkConfig.detect(ws)
    return OrchestrationService(ws, cfg.workflows, ArtifactMapper(ws), ModelRouter(cfg.model_profiles), LogMapper(ws))


def _all_workflows():
    ws = _workspace()
    return [(str(wf.id), wf) for wf in FrameworkConfig.detect(ws).workflows.all()]


_WORKFLOWS = _all_workflows()
_IDS = [wid for wid, _ in _WORKFLOWS]


@pytest.mark.parametrize("wid,workflow", _WORKFLOWS, ids=_IDS)
def test_workflow_drives_to_done_on_policy(wid: str, workflow) -> None:
    ws = _workspace()
    engine = _engine(ws)
    router = engine.router
    completed: set[str] = set()
    budget = len(workflow.steps) + 5
    seen_done = False
    for _ in range(budget):
        action = engine.next_action(workflow, completed)
        kind = action["action"]
        assert kind != "error", f"{wid}: engine error — {action.get('reason')}"
        if kind == "done":
            seen_done = True
            break
        if kind == "halt":
            # any halt (blocked / unroutable / off-policy) is a real defect in a dry-run.
            raise AssertionError(f"{wid}: halted on {action.get('reason')} at {action.get('step')}")
        if kind == "propose":
            # a delegate is PROPOSED, never dispatched — the dry-run assents (the user says yes)
            # and the delegate step completes when the suborchestration returns.
            assert action.get("delegates_to"), f"{wid}: propose without a suborchestration id"
            completed.add(action["step"])
            continue
        assert kind == "dispatch", f"{wid}: unexpected action {kind}"
        model = action.get("model")
        actor = str(action.get("actor", "")).lstrip("@")
        assert model, f"{wid}: step {action['step']} dispatched with no resolved model"
        assert router.validate_dispatch(model) is None, (
            f"{wid}: step {action['step']} off-policy model {model!r} for actor {actor!r}"
        )
        completed.add(action["step"])
    assert seen_done, f"{wid}: did not reach done within {budget} steps (deadlock or cycle?)"


@pytest.mark.parametrize("wid,workflow", _WORKFLOWS, ids=_IDS)
def test_workflow_has_no_facilitate_steps(wid: str, workflow) -> None:
    """The harness is the sole implicit mediator — no explicit `facilitate` mediation step
    survives (steps carry no `kind` taxonomy at all now; this asserts none was reintroduced)."""
    facilitate = [step.raw.get("kind") for step in workflow.steps if step.raw.get("kind") == "facilitate"]
    assert not facilitate, f"{wid}: facilitate steps still present (mediation must be implicit)"
