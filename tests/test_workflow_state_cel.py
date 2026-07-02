"""Workflow state-CEL TEST — every `type: state` condition's CEL references real schema props.

DESIGN-TIME framework test: for every authored workflow.yaml, each `type: state` condition's
`set_selector.set_query` and `set_predicate` must (a) name aliases bound to known artifact schemas,
(b) compile as CEL, and (c) reference only `<artifact>.<property>` names declared by that alias's
schema. This is the schema-property gate that runs alongside the workflow constitution.

It asserts two things:
  1. every authored workflow passes the gate (guards real workflows as state conditions land), and
  2. the gate actually catches violations (synthetic bad workflows are rejected) — so the guard in
     (1) is not vacuously green.

Run:  ``python3 harness/tests/test_workflow_state_cel.py``   (from the framework root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Workflow
from persistence import SchemaRepository, WorkflowRepository, Workspace
from services import WorkflowChecker


def _checker() -> WorkflowChecker:
    ws = Workspace.detect()
    return WorkflowChecker(ws, WorkflowRepository(ws), SchemaRepository(ws))


def _workflow(*conditions: dict) -> Workflow:
    """A one-step synthetic workflow carrying the given conditions."""
    data = {
        "workflow": {
            "id": "synthetic",
            "facilitator": "@x",
            "steps": [{"id": "s1", "kind": "author", "actor": "@x", "conditions": list(conditions)}],
        }
    }
    return Workflow(data, Path("synthetic/workflow.yaml"))


def _state(cond_id: str, artifact_types: list[dict], set_query: str, set_predicate: str | None) -> dict:
    cond: dict = {
        "id": cond_id,
        "kind": "postcondition",
        "type": "state",
        "set_selector": {"set_type": "artifact", "artifact_types": artifact_types, "set_query": set_query},
    }
    if set_predicate is not None:
        cond["set_predicate"] = set_predicate
    return cond


# --- (1) every authored workflow passes the state-CEL gate ----------------------
def test_authored_workflows_state_cel_valid() -> None:
    report = _checker().check()
    state_errors = [
        f
        for f in report.findings
        if f.severity == "error" and any(k in f.message for k in ("set_query", "set_predicate", "set_selector", "unknown schema_id"))
    ]
    assert not state_errors, "\n".join(f"{f.path}: {f.message}" for f in state_errors)


# --- (2) the gate catches violations (not vacuously green) ----------------------
def _findings(*conditions: dict) -> list[tuple[str, str]]:
    return _checker().state_condition_findings(_workflow(*conditions))


def test_valid_state_condition_passes() -> None:
    good = _state(
        "c_ok",
        [{"alias": "epic", "schema_id": "epic"}],
        "epic.filter(e, e.status == 'done')",
        "selected.all(e, e.status == 'done')",
    )
    assert _findings(good) == []


def test_undeclared_property_in_predicate_is_rejected() -> None:
    bad = _state(
        "c_pred",
        [{"alias": "epic", "schema_id": "epic"}],
        "epic",
        "selected.all(e, e.ghost == 1)",
    )
    findings = _findings(bad)
    assert findings and "set_predicate" in findings[0][1] and "ghost" in findings[0][1], findings


def test_undeclared_property_in_query_is_rejected() -> None:
    bad = _state(
        "c_query",
        [{"alias": "feature", "schema_id": "feature"}],
        "feature.filter(f, f.ghost == 'x')",
        "size(selected) > 0",
    )
    findings = _findings(bad)
    assert findings and "set_query" in findings[0][1] and "ghost" in findings[0][1], findings


def test_unknown_schema_id_is_rejected() -> None:
    bad = _state(
        "c_schema",
        [{"alias": "x", "schema_id": "not-a-real-schema"}],
        "x",
        "size(selected) > 0",
    )
    findings = _findings(bad)
    assert findings and "unknown schema_id" in findings[0][1], findings


def test_missing_set_query_is_rejected() -> None:
    cond = {
        "id": "c_noquery",
        "kind": "postcondition",
        "type": "state",
        "set_selector": {"set_type": "artifact", "artifact_types": [{"alias": "epic", "schema_id": "epic"}]},
        "set_predicate": "size(selected) > 0",
    }
    findings = _findings(cond)
    assert findings and "set_query is required" in findings[0][1], findings


def test_multi_alias_condition_passes() -> None:
    good = _state(
        "c_multi",
        [{"alias": "epic", "schema_id": "epic"}, {"alias": "feature", "schema_id": "feature"}],
        "feature.filter(f, f.parent_epic != '')",
        "selected.all(f, f.status != 'blocked')",
    )
    assert _findings(good) == []


def main() -> int:
    checks = [
        ("authored workflows state-cel valid", test_authored_workflows_state_cel_valid),
        ("valid state condition passes", test_valid_state_condition_passes),
        ("undeclared prop in predicate rejected", test_undeclared_property_in_predicate_is_rejected),
        ("undeclared prop in query rejected", test_undeclared_property_in_query_is_rejected),
        ("unknown schema_id rejected", test_unknown_schema_id_is_rejected),
        ("missing set_query rejected", test_missing_set_query_is_rejected),
        ("multi-alias condition passes", test_multi_alias_condition_passes),
    ]
    failures = 0
    for name, fn in checks:
        try:
            fn()
            print(f"  ok  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
    print(f"\n{'FAIL' if failures else 'pass'}: {failures} state-cel violation(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
