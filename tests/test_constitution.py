"""Workflow-constitution TEST — every workflow.yaml passes the contract checks.

DESIGN-TIME framework test that replaces the former ``check-contracts`` CLI command: validates
every ``workflow.yaml`` against ``workflow.schema.json`` plus the semantic rules JSON Schema can't
express — unique step ids, resolvable + acyclic ``after`` references, and every ``cel`` expression
compiling. This is the workflow constitution gate run by ``make verify``.

Run:  ``python3 harness/tests/test_constitution.py``   (from the framework root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Workflow
from mappers import SchemaRepository, WorkflowRepository, Workspace
from services import WorkflowChecker


def _checker() -> WorkflowChecker:
    ws = Workspace.detect()
    return WorkflowChecker(ws, WorkflowRepository(ws), SchemaRepository(ws))


def test_workflow_contracts() -> None:
    report = _checker().check()
    errors = [f for f in report.findings if f.severity == "error"]
    assert not errors, "\n".join(f"{f.path}: {f.message}" for f in errors)


def test_duplicate_condition_id_detected() -> None:
    """The gate flags two conditions in the same step that share an id (the run-log handle)."""
    workflow = Workflow(
        {
            "workflow": {
                "id": "synthetic",
                "facilitator": "@x",
                "steps": [
                    {
                        "id": "s1",
                        "kind": "author",
                        "actor": "@x",
                        "conditions": [
                            {"id": "dup", "kind": "precondition", "type": "after", "step_id": "s0"},
                            {"id": "dup", "kind": "postcondition", "type": "after", "step_id": "s0"},
                        ],
                    }
                ],
            }
        },
        Path("synthetic/workflow.yaml"),
    )
    assert _checker().duplicate_condition_ids(workflow) == [("s1", "dup")]


def main() -> int:
    report = _checker().check()
    errors = [f for f in report.findings if f.severity == "error"]
    for finding in errors:
        print(f"FAIL  {finding.path}: {finding.message}")
    print(f"\n{'FAIL' if errors else 'pass'}: {len(errors)} workflow contract error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
