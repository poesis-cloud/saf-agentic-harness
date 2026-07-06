"""Workflow-constitution TEST — every workflow configuration passes the config-plane gate.

DESIGN-TIME framework test. Loading a workflow configuration IS validating it (contract schema +
structural semantics, in `WorkflowCatalog.load`); the one rule needing the artifact schema catalog
— static `type: state` CEL validation — is the `CelEvaluator`'s and runs here over the whole
catalog. This is the workflow constitution gate run by ``make verify``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config import FrameworkConfig, SchemaCatalog, Workflow, WorkflowCatalog
from mappers import Workspace
from services import CelEvaluator


def test_every_workflow_configuration_is_valid() -> None:
    """validate_all covers contract + structural semantics for the whole workflows family."""
    report = FrameworkConfig.detect(Workspace.detect()).validate_all()
    errors = [f for f in report.findings if f.severity == "error"]
    assert not errors, "\n".join(f"{f.path}: {f.message}" for f in errors)


def test_every_state_condition_references_declared_properties() -> None:
    ws = Workspace.detect()
    config = FrameworkConfig.detect(ws)
    cel = CelEvaluator(ws, None, config.schemas)
    findings: list[str] = []
    for workflow in config.workflows.all():
        for step_id, message in cel.state_condition_findings(workflow):
            findings.append(f"{workflow.id}/{step_id}: {message}")
    assert not findings, "\n".join(findings)


def test_duplicate_condition_id_detected() -> None:
    """The config gate flags two conditions in the same step that share an id (the run-log handle)."""
    workflow = Workflow(
        {
            "workflow": {
                "id": "synthetic",
                "facilitator": "@x",
                "steps": [
                    {
                        "id": "s1",
                        "actor": "@x",
                        "conditions": [
                            {"id": "dup", "kind": "precondition", "type": "after", "step_id": "s1"},
                            {"id": "dup", "kind": "postcondition", "type": "after", "step_id": "s1"},
                        ],
                    }
                ],
            }
        },
        Path("synthetic/workflow.yaml"),
    )
    report = WorkflowCatalog._semantic_report(workflow, "synthetic")
    assert any("duplicate condition id" in f.message for f in report.findings)
