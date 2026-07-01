"""Framework structural-invariant TESTS over the workflow constitution.

DESIGN-TIME framework tests — deliberately NOT runtime harness functions. They assert the two
structural invariants and cross-workflow integrity over every Poesis-owned ``workflow.yaml``:

1. root-completeness    — every root orchestration has a ``rank: root`` workflow declaring ``drives``;
2. one-actor-per-step   — every step names exactly one hat (Invariant 1);
3. delegates_to resolves — every ``delegates_to`` points to a real sub-workflow;
4. fsm/CEL coherence    — every ``status == "X"`` in a root's CEL uses a state declared in its ``fsm``;
5. instruction URI resolves — every invariant's ``instruction`` value points to an existing file.

The harness is the sole implicit mediator (Design invariant: one log = run journal; the orchestrate
engine dispatches one step at a time), so there is no explicit ``facilitate``/mediation step and no
orthogonal ``orchestrator-mediation`` edge rule — participant→participant ``after`` edges are legal.

These checks read only the Poesis-owned workflows (never user portfolio data), so they live apart
from the runtime harness services and run via ``make test`` — separate from the ``make verify``
contract gate. They consume the harness's OOP API: a ``Workspace`` and the ``WorkflowRepository``
mapping each workflow.yaml to a ``Workflow`` entity.

Run:  ``python3 harness/tests/test_workflow_invariants.py``   (from the framework root)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Make the top-level src modules importable: add ``harness/src`` to sys.path so
# ``python3 harness/tests/test_workflow_invariants.py`` resolves them from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Workflow
from persistence import Workspace, WorkflowRepository

ROOT_ORCHESTRATIONS = (
    "layers/portfolio/workflow",
    "layers/program/workflow",
    "layers/team/workflow",
)

# A single actor: one ``@hat`` or the human ``central-supervisor``, each with an optional "(… hat)"
# note. Anything carrying " or ", "/", or "," is a multi-actor hedge and must FAIL Invariant 1.
_ACTOR_RE = re.compile(r"^(@[a-z][a-z0-9-]*|central-supervisor)(\s*\([^)]*\))?$")
_STATUS_EQ_RE = re.compile(r'status\s*==\s*"([^"]+)"')


def _workspace() -> Workspace:
    return Workspace.detect()


def _workflows(workspace: Workspace) -> list[Workflow]:
    return WorkflowRepository(workspace).all()


def _norm(actor: object) -> str:
    return str(actor if actor is not None else "").strip()


# --- the six structural checks (each returns a list of human-readable violations) --------------
def violations_root_completeness(workspace: Workspace) -> list[str]:
    out: list[str] = []
    repo = WorkflowRepository(workspace)
    for oid in ROOT_ORCHESTRATIONS:
        path = workspace.skills_root / oid / "workflow.yaml"
        if not path.is_file():
            out.append(f"{oid}: missing root workflow.yaml")
            continue
        workflow = repo.load(path)
        if workflow.rank != "root":
            out.append(f"{oid}/workflow.yaml: rank is {workflow.rank!r}, expected 'root'")
        if not workflow.drives:
            out.append(f"{oid}/workflow.yaml: root workflow is missing `drives`")
    return out


def violations_one_actor(workspace: Workspace) -> list[str]:
    out: list[str] = []
    for workflow in _workflows(workspace):
        label = workspace.label(workflow.path, workspace.skills_root)
        for step in workflow.steps:
            actor = _norm(step.actor)
            if not _ACTOR_RE.match(actor):
                out.append(f"{label}: step {step.raw_id!r} actor {actor!r} is not a single hat (Invariant 1)")
    return out


def violations_delegates_to(workspace: Workspace) -> list[str]:
    out: list[str] = []
    for workflow in _workflows(workspace):
        label = workspace.label(workflow.path, workspace.skills_root)
        for step in workflow.steps:
            target = step.delegates_to
            if not target:
                continue
            resolved = workflow.path.parent / str(target) / "workflow.yaml"
            if not resolved.is_file():
                out.append(f"{label}: step {step.raw_id!r} delegates_to {target!r} → no workflow.yaml at {target}/")
    return out


def violations_fsm_coherence(workspace: Workspace) -> list[str]:
    out: list[str] = []
    for workflow in _workflows(workspace):
        if not workflow.is_root:
            continue
        fsm = workflow.fsm
        if not fsm:
            continue
        label = workspace.label(workflow.path, workspace.skills_root)
        for step in workflow.steps:
            for cid, expr in step.cel_exprs:
                for status in _STATUS_EQ_RE.findall(str(expr)):
                    if status not in fsm:
                        out.append(
                            f"{label}: step {step.raw_id!r} condition {cid!r} uses status "
                            f"{status!r} not in declared fsm {sorted(fsm)}"
                        )
    return out


def violations_instruction_uri_resolves(workspace: Workspace) -> list[str]:
    out: list[str] = []
    for workflow in _workflows(workspace):
        label = workspace.label(workflow.path, workspace.skills_root)
        for step in workflow.steps:
            for cond in step.conditions:
                if not cond.is_instruction:
                    continue
                uri = cond.value
                if not (workflow.path.parent / uri).is_file():
                    out.append(f"{label}: step {step.raw_id!r} condition {cond.id!r} instruction URI {uri!r} → no file")
    return out


_CHECKS = (
    ("root-completeness", violations_root_completeness),
    ("one-actor-per-step (Invariant 1)", violations_one_actor),
    ("delegates_to resolves", violations_delegates_to),
    ("fsm/CEL status coherence", violations_fsm_coherence),
    ("instruction URI resolves", violations_instruction_uri_resolves),
)


# --- pytest entry points (discovered if pytest is available; assert-based) ----------------------
def test_every_root_has_workflow() -> None:
    violations = violations_root_completeness(_workspace())
    assert not violations, "\n".join(violations)


def test_one_actor_per_step() -> None:
    violations = violations_one_actor(_workspace())
    assert not violations, "\n".join(violations)


def test_delegates_to_resolves() -> None:
    violations = violations_delegates_to(_workspace())
    assert not violations, "\n".join(violations)


def test_fsm_status_coherence() -> None:
    violations = violations_fsm_coherence(_workspace())
    assert not violations, "\n".join(violations)


def test_instruction_uri_resolves() -> None:
    violations = violations_instruction_uri_resolves(_workspace())
    assert not violations, "\n".join(violations)


# --- standalone runner (no pytest dependency; used by `make test`) ------------------------------
def main() -> int:
    workspace = _workspace()
    total = 0
    for name, check in _CHECKS:
        violations = check(workspace)
        total += len(violations)
        if violations:
            print(f"FAIL  {name}: {len(violations)} violation(s)")
            for violation in violations:
                print(f"        - {violation}")
        else:
            print(f"pass  {name}")
    verdict = "FAIL" if total else "pass"
    print(f"\n{verdict}: {total} structural violation(s) across the workflow constitution")
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
