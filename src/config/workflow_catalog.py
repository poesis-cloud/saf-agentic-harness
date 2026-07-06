"""WorkflowCatalog — loads and validates the conf/workflows/ configuration family.

Each `conf/workflows/*.workflow.conf.yaml` file is one workflow configuration. Loading IS
validating (the config plane's rule): the file is parsed and checked against
`harness/contracts/workflow.conf.schema.json` (via `ConfigLoader.load_path`), then the semantic
rules JSON Schema cannot express are enforced in the same act — a non-empty steps model, unique
step ids, resolvable `after` references, unique condition ids within a step, and an acyclic
`after` DAG. Any violation raises `ConfigError`. The one semantic rule that needs more than the
configuration itself — statically validating `type: state` CEL against the artifact schema
catalog — lives on the `CelEvaluator` service and is enforced by the constitution gate.
"""

from __future__ import annotations

from pathlib import Path

from models import Report

from .errors import ConfigError
from .loader import ConfigLoader
from .workflow import Workflow


class WorkflowCatalog:
    """The configuration catalog for workflows: paths, load (parse + validate), all, find."""

    def __init__(self, framework_root: Path, loader: ConfigLoader) -> None:
        self.framework_root = framework_root
        self.loader = loader
        self._all: list[Workflow] | None = None

    def paths(self) -> list[Path]:
        return sorted((self.framework_root / "conf" / "workflows").glob("*.workflow.conf.yaml"))

    def load(self, path: Path) -> Workflow:
        """Parse + contract-validate + semantically validate one workflow configuration file
        (raises ConfigError on any violation)."""
        workflow = Workflow(self.loader.load_path(path, "workflow"), path)
        report = self._semantic_report(workflow, str(path))
        if report.has_errors():
            raise ConfigError(report)
        return workflow

    def all(self) -> list[Workflow]:
        if self._all is None:
            self._all = [self.load(path) for path in self.paths()]
        return self._all

    def find(self, orchestration_id: str) -> Workflow | None:
        for workflow in self.all():
            if str(workflow.id) == orchestration_id:
                return workflow
        return None

    # --- the advisory sequence graph ------------------------------------------
    def successors(self, workflow_id: str) -> list[str]:
        """The workflows that NATURALLY come after ``workflow_id`` (declared via their `after`)."""
        return sorted(str(wf.id) for wf in self.all() if workflow_id in wf.after_ids)

    def eligible(self, completed: set[str]) -> list[str]:
        """The next NATURAL workflows given the completed set: not yet complete, and every
        advisory predecessor complete. Advisory only — the user may run any workflow anytime."""
        return sorted(
            str(wf.id)
            for wf in self.all()
            if str(wf.id) not in completed and all(pred in completed for pred in wf.after_ids)
        )

    def catalog_report(self) -> Report:
        """Catalog-level semantic validation of the advisory sequence: every `after` reference
        resolves to a cataloged workflow id, and the advisory graph is acyclic."""
        report = Report()
        ids = {str(wf.id) for wf in self.all()}
        graph: dict[str, list[str]] = {}
        for wf in self.all():
            graph[str(wf.id)] = wf.after_ids
            for pred in wf.after_ids:
                if pred not in ids:
                    report.error(str(wf.path), f"workflow after references unknown workflow {pred!r}")
        # cycle detection over the advisory graph
        white, grey, black = 0, 1, 2
        color = {node: white for node in graph}

        def visit(node: str) -> bool:
            color[node] = grey
            for dep in graph.get(node, []):
                if dep not in graph:
                    continue
                if color[dep] == grey or (color[dep] == white and visit(dep)):
                    return True
            color[node] = black
            return False

        for node in graph:
            if color[node] == white and visit(node):
                report.error("conf/workflows", f"the advisory workflow `after` graph has a cycle through {node!r}")
                break
        return report

    # --- semantic rules JSON Schema cannot express ----------------------------
    @staticmethod
    def _semantic_report(workflow: Workflow, label: str) -> Report:
        report = Report()
        steps = workflow.steps
        if not steps:
            report.error(label, "workflow has no steps[] (every workflow is a steps model under the `workflow` root)")
            return report
        # unique step ids
        ids = [step.id for step in steps if step.raw_id is not None]
        seen: set[str] = set()
        for sid in ids:
            if sid in seen:
                report.error(label, f"duplicate step id {sid!r}")
            seen.add(sid)
        # `after` references resolve
        id_set = set(ids)
        for step in steps:
            for dep in step.after_ids:
                if dep not in id_set:
                    report.error(label, f"step {step.id!r}: after references unknown step {dep!r}")
        # condition ids are the run-log / findings / check-step handle: unique within a step
        # (they may legitimately recur across different steps).
        for step in steps:
            seen_conditions: set[str] = set()
            for condition in step.conditions:
                cid = condition.id
                if cid is None:
                    continue
                if cid in seen_conditions:
                    report.error(label, f"step {step.id!r}: duplicate condition id {cid!r} (condition ids must be unique within a step)")
                seen_conditions.add(cid)
        # every step is a dispatchable agent turn: at least one positive capability weight
        # (all-zero weights are unroutable — the old gate convention died with the gate kind).
        for step in steps:
            if not any(weight > 0 for weight in step.capabilities.values()):
                report.error(label, f"step {step.id!r}: capabilities must carry at least one positive weight (every step is a dispatchable agent turn)")
        # acyclic `after` DAG
        cycle = workflow.cycle()
        if cycle:
            report.error(label, f"`after` graph has a cycle: {' -> '.join(cycle)}")
        return report


__all__ = ["WorkflowCatalog"]
