"""Unit tests — OrchestrationService: the pure sequencing core (`next_action`) and the
`orchestrate` entry (workflow resolution + cycle guard). One test class per src class; the
router and catalog are synthetic doubles — no filesystem."""

from __future__ import annotations

from config import ModelProfiles, Workflow
from mappers import LogMapper, Workspace
from services import ModelRouter, OrchestrationService

CAPS = {"deep-reasoning": 7, "writing-quality": 8}

CATALOG = ModelProfiles({"models": [
    {"id": "big", "cost_rank": 8, "capability_scores": {"deep-reasoning": 9, "writing-quality": 9}},
    {"id": "small", "cost_rank": 2, "capability_scores": {"deep-reasoning": 4, "writing-quality": 5}},
]})


class _StubCatalog:
    """A WorkflowCatalog double: find/all + the advisory graph over an in-memory list."""

    def __init__(self, *workflows: Workflow) -> None:
        self._workflows = list(workflows)

    def find(self, orchestration_id: str) -> Workflow | None:
        return next((w for w in self._workflows if str(w.id) == orchestration_id), None)

    def all(self) -> list[Workflow]:
        return list(self._workflows)

    def successors(self, workflow_id: str) -> list[str]:
        return sorted(str(w.id) for w in self._workflows if workflow_id in w.after_ids)

    def eligible(self, completed: set[str]) -> list[str]:
        return sorted(
            str(w.id) for w in self._workflows
            if str(w.id) not in completed and all(p in completed for p in w.after_ids)
        )


def _workflow(steps: list[dict], wid: str = "wf") -> Workflow:
    return Workflow({"workflow": {"id": wid, "steps": steps}})


def _engine(*workflows: Workflow) -> OrchestrationService:
    return OrchestrationService(None, _StubCatalog(*workflows), None, ModelRouter(CATALOG), None)


LINEAR = [
    {"id": "draft", "actor": "@dev", "artifacts": ["story"], "capabilities": CAPS},
    {"id": "review", "actor": "@qa", "artifacts": ["review"], "capabilities": CAPS,
     "conditions": [{"type": "after", "step_id": "draft"}]},
    {"id": "approve", "actor": "@owner",
     "capabilities": CAPS, "conditions": [{"type": "after", "step_id": "review"}]},
    {"id": "land", "actor": "@owner", "capabilities": CAPS,
     "conditions": [{"type": "after", "step_id": "approve"}]},
]


class TestOrchestrationService:
    # --- next_action: the pure core -----------------------------------------
    def test_first_eligible_step_dispatches_with_routing_binding(self):
        action = _engine().next_action(_workflow(LINEAR), completed=set(), unit="u-1", run="r-1")
        assert action["action"] == "dispatch"
        assert action["step"] == "draft"
        assert action["model"] == "big"
        assert action["routing"]["score"] > 0
        assert action["prompt_context"]["unit"] == "u-1"

    def test_dispatch_respects_authored_order_after_completion(self):
        action = _engine().next_action(_workflow(LINEAR), completed={"draft"})
        assert action["step"] == "review"

    def test_catalog_propose_lists_eligible_next_workflows(self, tmp_path):
        planning = _workflow(LINEAR, wid="planning")
        review = Workflow({"workflow": {"id": "review", "after": ["planning"], "steps": LINEAR}})
        engine = self._journaled_engine(tmp_path, planning, review)
        journal = engine.workspace.run_journal("r-1")
        for step in ("draft", "review", "approve", "land"):
            engine.logs.append(journal, {"orchestration": "planning", "step": step, "status": "completed"})
        action = engine.orchestrate(run="r-1")
        assert action["action"] == "propose"
        assert action["completed"] == ["planning"]
        assert action["eligible"] == ["review"]

    def test_done_carries_the_advisory_successors(self, tmp_path):
        planning = _workflow(LINEAR, wid="planning")
        review = Workflow({"workflow": {"id": "review", "after": ["planning"], "steps": LINEAR}})
        engine = self._journaled_engine(tmp_path, planning, review)
        journal = engine.workspace.run_journal("r-1")
        for step in ("draft", "review", "approve", "land"):
            engine.logs.append(journal, {"orchestration": "planning", "step": step, "status": "completed"})
        action = engine.orchestrate("planning", run="r-1")
        assert action["action"] == "done"
        assert action["propose"] == ["review"]

    def test_advisory_sequence_never_constrains(self, tmp_path):
        # a workflow whose advisory predecessors are NOT complete still drives on request.
        review = Workflow({"workflow": {"id": "review", "after": ["planning"], "steps": LINEAR}})
        engine = self._journaled_engine(tmp_path, review)
        action = engine.orchestrate("review", run="r-1")
        assert action["action"] == "dispatch"

    def test_done_when_every_step_is_complete(self):
        action = _engine().next_action(_workflow(LINEAR), completed={"draft", "review", "approve", "land"})
        assert action["action"] == "done"

    def test_blocked_when_no_step_is_eligible(self):
        steps = [{"id": "only", "actor": "@dev", "capabilities": CAPS,
                  "conditions": [{"type": "after", "step_id": "missing"}]}]
        action = _engine().next_action(_workflow(steps), completed=set())
        assert action["action"] == "halt"
        assert action["reason"] == "blocked"
        assert action["unmet_predecessors"] == ["missing"]

    def test_unroutable_step_halts_instead_of_passing_auto(self):
        steps = [{"id": "zero", "actor": "@dev", "capabilities": {"coding": 0}}]
        action = _engine().next_action(_workflow(steps), completed=set())
        assert action["action"] == "halt"
        assert action["reason"] == "unroutable"
        assert action["model"] is None

    def test_workflow_without_steps_is_an_error(self):
        action = _engine().next_action(_workflow([]), completed=set())
        assert action["action"] == "error"

    # --- orchestrate: the entry ----------------------------------------------
    def test_orchestrate_resolves_workflow_by_id(self):
        engine = _engine(_workflow(LINEAR, wid="team"))
        action = engine.orchestrate("team", unit="u-1")
        assert action["action"] == "dispatch"
        assert action["workflow"] == "team"

    def test_orchestrate_unknown_workflow_is_an_error(self):
        action = _engine().orchestrate("ghost")
        assert action["action"] == "error"
        assert "ghost" in action["reason"]

    # --- the journal cursor ---------------------------------------------------
    def _journaled_engine(self, tmp_path, *workflows: Workflow) -> OrchestrationService:
        ws = Workspace(tmp_path, tmp_path / "workspace")
        return OrchestrationService(ws, _StubCatalog(*workflows), None, ModelRouter(CATALOG), LogMapper(ws))

    def test_orchestrate_derives_completion_from_the_run_journal(self, tmp_path):
        engine = self._journaled_engine(tmp_path, _workflow(LINEAR, wid="team"))
        journal = engine.workspace.run_journal("r-1")
        engine.logs.append(journal, {"orchestration": "team", "step": "draft", "status": "completed"})
        action = engine.orchestrate("team", run="r-1", unit="u-1")
        assert action["action"] == "dispatch"
        assert action["step"] == "review"

    def test_journal_replay_reopens_a_step(self, tmp_path):
        # latest-wins: draft completed then reopened -> the cursor points back at draft.
        engine = self._journaled_engine(tmp_path, _workflow(LINEAR, wid="team"))
        journal = engine.workspace.run_journal("r-1")
        engine.logs.append(journal, {"orchestration": "team", "step": "draft", "status": "completed"})
        engine.logs.append(journal, {"orchestration": "team", "step": "draft", "status": "reopened"})
        action = engine.orchestrate("team", run="r-1")
        assert action["step"] == "draft"

    def test_orchestrate_without_a_run_starts_from_the_first_step(self, tmp_path):
        engine = self._journaled_engine(tmp_path, _workflow(LINEAR, wid="team"))
        action = engine.orchestrate("team")
        assert action["step"] == "draft"

    def test_fully_journaled_workflow_is_done(self, tmp_path):
        engine = self._journaled_engine(tmp_path, _workflow(LINEAR, wid="team"))
        journal = engine.workspace.run_journal("r-1")
        for step in ("draft", "review", "approve", "land"):
            engine.logs.append(journal, {"orchestration": "team", "step": step, "status": "completed"})
        assert engine.orchestrate("team", run="r-1")["action"] == "done"

    def test_orchestrate_rejects_a_cyclic_after_dag(self):
        cyclic = _workflow([
            {"id": "a", "actor": "@x", "capabilities": CAPS,
             "conditions": [{"type": "after", "step_id": "b"}]},
            {"id": "b", "actor": "@x", "capabilities": CAPS,
             "conditions": [{"type": "after", "step_id": "a"}]},
        ], wid="loop")
        action = _engine(cyclic).orchestrate("loop")
        assert action["action"] == "error"
        assert "cycle" in action["reason"]
