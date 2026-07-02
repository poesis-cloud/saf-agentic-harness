"""WorkflowChecker — validates the workflow constitution (the `make verify` contract gate)."""

from __future__ import annotations

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from models import Report, Workflow
from mappers import SchemaMapper, WorkflowMapper, Workspace
from .cel_evaluator import CelEvaluator


class WorkflowChecker:
    """The workflow-constitution check (run by the pytest suite). Validates every
    workflow.yaml against workflow.schema.json plus the semantic rules JSON Schema can't
    express — unique step ids, unique condition ids within a step, resolvable `after`
    references, an acyclic `after` DAG, every `cel` expression compiling, and every
    `type: state` condition's `set_query` / `set_predicate` referencing only properties
    declared by the aliased artifact schemas."""

    def __init__(self, workspace: Workspace, workflows: WorkflowMapper, schemas: SchemaMapper) -> None:
        self.workspace = workspace
        self.workflows = workflows
        self.schemas = schemas
        # schema-only evaluator (no portfolio) for design-time state-CEL validation
        self._cel = CelEvaluator(workspace, None, schemas)

    def check(self) -> Report:
        report = Report()
        root = self.workspace.skills_root
        schema = self.schemas.workflow_schema()
        if schema is None:
            report.error(self.workspace.label(self.schemas.workflow_schema_path(), root.parent), "workflow.schema.json not found")
            return report
        if yaml is None:
            report.error("check-contracts", "PyYAML is required to parse workflows")
            return report
        validator = jsonschema.Draft7Validator(schema) if jsonschema is not None else None
        for path in self.workflows.paths():
            label = self.workspace.label(path, root.parent)
            try:
                data = yaml.safe_load(self.workspace.read_text(path))
            except Exception as exc:
                report.error(label, f"invalid YAML: {exc}")
                continue
            if not isinstance(data, dict):
                report.error(label, "workflow is not a YAML mapping")
                continue
            workflow = Workflow(data, path)
            steps = workflow.steps
            if not steps:
                report.error(label, "workflow has no steps[] (every workflow is a steps model under the `workflow` root)")
                continue
            if validator is not None:
                for err in sorted(validator.iter_errors(data), key=lambda item: list(item.absolute_path)):
                    report.error(label, f"workflow schema violation: {self.schemas.format_schema_error(err)}")
            ids = [step.id for step in steps if step.raw_id is not None]
            seen: set[str] = set()
            for sid in ids:
                if sid in seen:
                    report.error(label, f"duplicate step id {sid!r}")
                seen.add(sid)
            id_set = set(ids)
            for step in steps:
                sid = step.id
                for dep in step.after_ids:
                    if dep not in id_set:
                        report.error(label, f"step {sid!r}: after references unknown step {dep!r}")
            # `type: state` conditions: statically validate set_query / set_predicate against the
            # aliased artifact schemas (aliases resolve, CEL compiles, and every property reference
            # is declared by its schema — an off-schema property is a hard error).
            for sid, message in self.state_condition_findings(workflow):
                report.error(label, f"step {sid!r} {message}")
            # condition ids are the run-log / findings / check-step handle, so they must be unique
            # within a step (they may legitimately recur across different steps).
            for sid, cid in self.duplicate_condition_ids(workflow):
                report.error(label, f"step {sid!r}: duplicate condition id {cid!r} (condition ids must be unique within a step)")
            cycle = workflow.cycle()
            if cycle:
                report.error(label, f"`after` graph has a cycle: {' -> '.join(cycle)}")
        return report

    def state_condition_findings(self, workflow: Workflow) -> list[tuple[str, str]]:
        """(step_id, message) for every `type: state` condition whose `set_selector` /
        `set_predicate` fails static schema validation — unknown alias schema, uncompilable CEL,
        or a property reference not declared by the aliased artifact schema. Reusable by the
        constitution gate and its tests; needs no portfolio (design-time schema check only)."""
        findings: list[tuple[str, str]] = []
        for step in workflow.steps:
            for condition in step.conditions:
                if condition.type == "state" and condition.set_selector is not None:
                    error = self._cel.validate_state_condition(condition.set_selector, condition.set_predicate)
                    if error:
                        findings.append((step.id, f"condition {condition.id!r}: {error}"))
        return findings

    def duplicate_condition_ids(self, workflow: Workflow) -> list[tuple[str, str]]:
        """(step_id, condition_id) for every condition id that repeats WITHIN a single step. A
        condition id is the handle used in the run log, findings, and check-step, so it must be
        unique within its step (ids may legitimately recur across different steps). Presence of
        the id itself is enforced by the schema (condition.required = [kind, id])."""
        dupes: list[tuple[str, str]] = []
        for step in workflow.steps:
            seen: set[str] = set()
            for condition in step.conditions:
                cid = condition.id
                if cid is None:
                    continue
                if cid in seen:
                    dupes.append((step.id, cid))
                seen.add(cid)
        return dupes
