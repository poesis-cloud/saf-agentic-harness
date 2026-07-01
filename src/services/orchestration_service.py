"""OrchestrationService — the `orchestrate <workflow-id>` engine (the harness `drive`).

Given a workflow id + a unit, the engine recomputes the step cursor from the unit's ARTIFACTS
(never from a prior log line — Design invariant 13: check-only determinism) and returns exactly one
of three actions:

* ``dispatch`` — the next eligible step, with its resolved ``{step, actor, model, skills, config,
  unit, output, prompt_context}`` binding (the model resolved deterministically via ``ModelRouter``
  from the step's static routing metadata: role / risk / complexity / tags / config).
* ``halt`` — a ``gate`` step is next (await the human ★ decision), or no step is eligible while the
  workflow is unfinished (an unmet predecessor / blocked precondition).
* ``done`` — every step's output artifact exists; the workflow is complete for this unit.

Sequencing is split in two: a pure, filesystem-independent core (``next_action`` over a workflow +
a set of completed step ids) and a thin cursor (``_completed_steps``) that derives that set from the
unit's artifacts. The engine never writes — it returns the action; the host commits it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from models import Workflow
from persistence import ArtifactRepository, WorkflowRepository, Workspace
from .model_router import ModelRouter


class OrchestrationService:
    """Resolves the next orchestration action for a (workflow, unit) from artifact state."""

    def __init__(
        self,
        workspace: Workspace,
        workflows: WorkflowRepository,
        artifacts: ArtifactRepository,
        router: ModelRouter,
    ) -> None:
        self.workspace = workspace
        self.workflows = workflows
        self.artifacts = artifacts
        self.router = router

    # --- entry point --------------------------------------------------------
    def orchestrate(self, workflow_id: str, run: str | None = None, unit: str | None = None) -> dict[str, Any]:
        """Resolve the next action for ``workflow_id`` acting on ``unit`` (an Epic/Feature/Story id)."""
        workflow = self.workflows.find(workflow_id)
        if workflow is None:
            return {"action": "error", "workflow": workflow_id, "reason": f"no workflow found for id {workflow_id!r}"}
        if workflow.cycle():
            cycle = " -> ".join(workflow.cycle())
            return {"action": "error", "workflow": workflow_id, "reason": f"the `after` DAG has a cycle: {cycle}"}
        completed = self._completed_steps(workflow, unit)
        return self.next_action(workflow, completed, unit=unit, run=run)

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
            return {"action": "done", "workflow": str(workflow.id), "unit": unit, "run": run}

        # First eligible step in authored order: every `after` predecessor already complete.
        for step in remaining:
            if all(pred in completed for pred in step.after_ids):
                if step.kind == "gate":
                    return {
                        "action": "halt",
                        "reason": "gate",
                        "workflow": str(workflow.id),
                        "step": step.id,
                        "actor": step.actor,
                        "unit": unit,
                        "run": run,
                    }
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
        binding = self._resolve_model(step)
        payload: dict[str, Any] = {
            "action": "dispatch",
            "workflow": str(workflow.id),
            "run": run,
            "step": step.id,
            "actor": step.actor,
            "kind": step.kind,
            "model": binding.get("model") if binding else None,
            "config": step.config or binding.get("config_profile") if binding else step.config,
            "skills": step.skills,
            "unit": unit,
            "output": step.output,
            "prompt_context": {
                "workflow": str(workflow.id),
                "step": step.id,
                "unit": unit,
                "output": step.output,
                "inputs": step.ref_values("input"),
            },
        }
        if binding is None:
            payload["action"] = "halt"
            payload["reason"] = "unroutable"
            payload["detail"] = "no model clears the step's tier floor in llm/map.yaml"
            return payload
        payload["routing"] = binding
        error = self.router.validate_dispatch(step.role, binding.get("model"))
        if error:
            payload["action"] = "halt"
            payload["reason"] = "off-policy-dispatch"
            payload["detail"] = error
        return payload

    def _resolve_model(self, step: Any) -> dict[str, Any] | None:
        floor = self.router.tier_floor(step.risk, step.complexity, self.router.role_default(step.role))
        return self.router.resolve(floor, step.tags, config_profile=step.config or None)

    # --- artifact cursor ----------------------------------------------------
    def _completed_steps(self, workflow: Workflow, unit: str | None) -> set[str]:
        """A step is complete when ALL of its resolvable `output` refs exist on disk for this unit.

        Logical-only outputs (no resolvable path — e.g. ``open_items[kind=...]``) cannot be sensed
        structurally, so such a step never counts complete via the cursor (it advances once a
        downstream concrete artifact appears, or is gated by an `after` chain)."""
        artifact = self.artifacts.resolve_unit(unit) if unit else None
        product = artifact.product_slug if artifact is not None else None
        contract_dir = workflow.path.parent if workflow.path is not None else None
        completed: set[str] = set()
        for step in workflow.steps:
            outputs = step.ref_values("output")
            resolvable = [self._resolve_ref(contract_dir, product, unit, ref) for ref in outputs]
            concrete = [path for path in resolvable if path is not None]
            if concrete and all(path.exists() for path in concrete):
                completed.add(step.id)
        return completed

    def _resolve_ref(self, contract_dir: Path | None, product: str | None, unit: str | None, ref: str) -> Path | None:
        """Resolve a structural output ref to a concrete repo path, or None for a logical ref."""
        if "{product}" in ref and not product:
            return None
        ref = ref.replace("{unit_id}", unit or "").replace("{product}", product or "")
        if "[" in ref or "/" not in ref:
            return None  # logical ref (epic.artifact.json, raw-idea, open_items[kind=...])
        if ref.startswith("artifacts/") and contract_dir is not None:
            return contract_dir / ref
        return self.workspace.portfolio_base / ref


__all__ = ["OrchestrationService"]
