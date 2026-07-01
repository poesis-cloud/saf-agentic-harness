"""WorkflowChecker — validates the workflow constitution and pins it to workflows.lock."""

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
from persistence import SchemaRepository, WorkflowRepository, Workspace
from .cel_evaluator import CelEvaluator


class WorkflowChecker:
    """The workflow-constitution check (run by the pytest suite). Validates every
    workflow.yaml against workflow.schema.json plus the semantic rules JSON Schema can't
    express — unique step ids, resolvable `after` references, an acyclic `after` DAG, and
    every `cel` expression compiling."""

    def __init__(self, workspace: Workspace, workflows: WorkflowRepository, schemas: SchemaRepository) -> None:
        self.workspace = workspace
        self.workflows = workflows
        self.schemas = schemas

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
                for cid, expr in step.cel_exprs:
                    _program, err = CelEvaluator.compile_expr(expr)
                    if err:
                        report.error(label, f"step {sid!r} condition {cid!r}: invalid expr: {err}")
            cycle = workflow.cycle()
            if cycle:
                report.error(label, f"`after` graph has a cycle: {' -> '.join(cycle)}")
        return report
