"""OrchestrationService — the `orchestrate [<workflow-id>]` engine (the harness `drive`).

With a workflow id + a unit, the engine recomputes the step cursor from the RUN JOURNAL (the
append-only record `check-step` writes: a step counts as completed when its LATEST journal line
for THAT workflow says so — replay re-opens a step) and returns exactly one action:

* ``dispatch`` — the next eligible step, with its resolved ``{step, actor, model, skills, unit,
  output, prompt_context}`` binding (the model resolved deterministically via ``ModelRouter``
  from the step's weighted ``capabilities`` map against the model catalog).
* ``halt`` — no step is eligible while the workflow is unfinished (an unmet predecessor /
  blocked precondition / unroutable step).
* ``done`` — every step is journaled complete; the payload carries ``propose`` — the advisory
  NEXT natural workflow(s) from the catalog's workflow-level `after` graph.

Without a workflow id, the engine PROPOSES: it derives the completed workflows from the run
journal and returns the eligible next natural ones. The sequence is advisory, never constraining
— the user may (re)run any workflow at any time; assent, not the DAG, starts a workflow.

Sequencing is split in two: a pure, filesystem-independent core (``next_action`` over a workflow +
a set of completed step ids) and a thin cursor (``_completed_steps``) that derives that set from
the run journal. The engine never writes — it returns the action; the host commits it.
"""

from __future__ import annotations

from typing import Any

from config import Workflow, WorkflowCatalog
from mappers import ArtifactMapper, LogMapper, Workspace
from .model_router import ModelRouter


class OrchestrationService:
    """Resolves the next orchestration action for a (workflow, unit) from the run journal."""

    def __init__(
        self,
        workspace: Workspace,
        workflows: WorkflowCatalog,
        artifacts: ArtifactMapper,
        router: ModelRouter,
        logs: LogMapper,
    ) -> None:
        self.workspace = workspace
        self.workflows = workflows
        self.artifacts = artifacts
        self.router = router
        self.logs = logs

    # --- entry point --------------------------------------------------------
    def orchestrate(self, workflow_id: str | None = None, run: str | None = None, unit: str | None = None) -> dict[str, Any]:
        """Resolve the next action for ``workflow_id`` acting on ``unit`` (an artifact slug),
        or — with no workflow id — PROPOSE the next natural workflow(s) from the catalog."""
        if workflow_id is None:
            return self.propose(run=run, unit=unit)
        workflow = self.workflows.find(workflow_id)
        if workflow is None:
            return {"action": "error", "workflow": workflow_id, "reason": f"no workflow found for id {workflow_id!r}"}
        if workflow.cycle():
            cycle = " -> ".join(workflow.cycle())
            return {"action": "error", "workflow": workflow_id, "reason": f"the `after` DAG has a cycle: {cycle}"}
        completed = self._completed_steps(run, workflow_id)
        return self.next_action(workflow, completed, unit=unit, run=run)

    def propose(self, run: str | None = None, unit: str | None = None) -> dict[str, Any]:
        """The advisory catalog-level proposal: the eligible next natural workflow(s), derived
        from the journal's completed workflows and the workflow-level `after` graph. Advisory
        only — the user may equally reiterate a completed workflow or go back; assent starts it."""
        completed = self._completed_workflows(run)
        return {
            "action": "propose",
            "reason": "advisory sequence — the user may take a proposed workflow, reiterate, or go back",
            "eligible": self.workflows.eligible(completed),
            "completed": sorted(completed),
            "unit": unit,
            "run": run,
        }

    # --- pure sequencing core (filesystem-independent) ----------------------
    def next_action(
        self,
        workflow: Workflow,
        completed: set[str],
        *,
        unit: str | None = None,
        run: str | None = None,
    ) -> dict[str, Any]:
        """Return the next action from a workflow + the set of completed step ids. Pure: no I/O."""
        steps = workflow.steps
        if not steps:
            return {"action": "error", "workflow": str(workflow.id), "reason": "workflow has no steps[]"}

        remaining = [step for step in steps if step.id not in completed]
        if not remaining:
            done: dict[str, Any] = {"action": "done", "workflow": str(workflow.id), "unit": unit, "run": run}
            successors = self.workflows.successors(str(workflow.id)) if hasattr(self.workflows, "successors") else []
            if successors:
                # advisory: the natural next workflow(s) — the user assents, reiterates, or goes back.
                done["propose"] = successors
            return done

        # First eligible step in authored order: every `after` predecessor already complete.
        for step in remaining:
            if all(pred in completed for pred in step.after_ids):
                return self._dispatch(workflow, step, unit, run)

        # Nothing eligible but work remains — predecessors are unmet (or were never produced).
        blocked = remaining[0]
        unmet = [pred for pred in blocked.after_ids if pred not in completed]
        return {
            "action": "halt",
            "reason": "blocked",
            "workflow": str(workflow.id),
            "step": blocked.id,
            "unmet_predecessors": unmet,
            "unit": unit,
            "run": run,
        }

    # --- dispatch payload ---------------------------------------------------
    def _dispatch(self, workflow: Workflow, step: Any, unit: str | None, run: str | None) -> dict[str, Any]:
        binding = self.router.resolve(step.capabilities)
        payload: dict[str, Any] = {
            "action": "dispatch",
            "workflow": str(workflow.id),
            "run": run,
            "step": step.id,
            "actor": step.actor,
            "model": binding.get("model") if binding else None,
            "skills": step.skills,
            "unit": unit,
            "artifacts": step.artifacts,
            "prompt_context": {
                "workflow": str(workflow.id),
                "step": step.id,
                "unit": unit,
                "artifacts": step.artifacts,
            },
        }
        if binding is None:
            payload["action"] = "halt"
            payload["reason"] = "unroutable"
            payload["detail"] = (f"step {step.id!r} has no positive capability weights (or the model "
                                  "catalog is empty) — nothing to score; the harness halts rather "
                                  "than passing Auto")
            return payload
        payload["routing"] = binding
        return payload

    # --- journal cursor -------------------------------------------------------
    def _completed_steps(self, run: str | None, workflow_id: str) -> set[str]:
        """The completed step ids OF ONE WORKFLOW, derived from the run journal
        (`workspace/logs/<run>.jsonl`): a step counts as complete when its LATEST journaled line
        for that orchestration reports status `completed` (latest-wins — a replayed/reopened step
        drops out). With no journal in scope the cursor is empty and orchestration starts from
        the first eligible step."""
        log = self.logs.read(self.workspace.run_journal(run)) if run else None
        return set(log.executed_steps(workflow_id)) if log is not None else set()

    def _completed_workflows(self, run: str | None) -> set[str]:
        """The workflows whose EVERY step is journaled complete — the catalog-level cursor the
        advisory proposal derives from."""
        log = self.logs.read(self.workspace.run_journal(run)) if run else None
        if log is None:
            return set()
        completed: set[str] = set()
        for workflow in self.workflows.all():
            step_ids = set(workflow.step_ids)
            if step_ids and step_ids <= set(log.executed_steps(str(workflow.id))):
                completed.add(str(workflow.id))
        return completed


__all__ = ["OrchestrationService"]
