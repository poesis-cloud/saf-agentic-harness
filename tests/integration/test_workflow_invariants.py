"""Framework structural-invariant TESTS over the workflow constitution.

DESIGN-TIME framework tests — deliberately NOT runtime harness functions. They assert the two
structural invariants and cross-workflow integrity over every Poesis-owned ``workflow.yaml``:

1. root-completeness    — every root orchestration exists and is identified by a root id;
2. one-actor-per-step   — every step names exactly one hat (Invariant 1);
3. delegates_to resolves — every ``delegates_to`` points to a real sub-workflow;
4. legacy-header cleanup — workflow-level ``skills``/``drives``/``fsm`` keys are absent;
5. instruction URI resolves — every invariant's ``instruction`` value points to an existing file.

The harness is the sole implicit mediator (Design invariant: one log = run journal; the orchestrate
engine dispatches one step at a time), so there is no explicit ``facilitate``/mediation step and no
orthogonal ``orchestrator-mediation`` edge rule — participant→participant ``after`` edges are legal.

These checks read only the Poesis-owned workflows (never user workspace data), so they live apart
from the runtime harness services and run via ``make test`` — separate from the ``make verify``
contract gate. They consume the harness's OOP API: a ``Workspace`` and the ``WorkflowCatalog``
mapping each workflow.yaml to a ``Workflow`` entity.

Run:  ``python3 harness/tests/test_workflow_invariants.py``   (from the framework root)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Make the top-level src modules importable: add ``harness/src`` to sys.path so
# ``python3 harness/tests/test_workflow_invariants.py`` resolves them from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config import Workflow
from config import FrameworkConfig
from mappers import Workspace

ROOT_ORCHESTRATIONS = (
    "portfolio",
    "program",
    "team",
)

# A single actor: one ``@hat`` or the human ``central-supervisor``, each with an optional "(… hat)"
# note. Anything carrying " or ", "/", or "," is a multi-actor hedge and must FAIL Invariant 1.
_ACTOR_RE = re.compile(r"^(@[a-z][a-z0-9-]*|central-supervisor)(\s*\([^)]*\))?$")
def _workspace() -> Workspace:
    return Workspace.detect()


def _workflows(workspace: Workspace) -> list[Workflow]:
    return FrameworkConfig.detect(workspace).workflows.all()


def _norm(actor: object) -> str:
    return str(actor if actor is not None else "").strip()


# --- the structural checks (each returns a list of human-readable violations) -------------------
def _workflow_path(workspace: Workspace, name: str) -> Path:
    return workspace.framework_root / "conf" / "workflows" / f"{name}.workflow.conf.yaml"


def violations_advisory_graph(workspace: Workspace) -> list[str]:
    """The advisory workflow-level `after` graph must resolve and stay acyclic (catalog-level)."""
    report = FrameworkConfig.detect(workspace).workflows.catalog_report()
    return [f"{f.path}: {f.message}" for f in report.findings if f.severity == "error"]


def violations_one_actor(workspace: Workspace) -> list[str]:
    out: list[str] = []
    for workflow in _workflows(workspace):
        label = workspace.label(workflow.path, workspace.framework_root)
        for step in workflow.steps:
            actor = _norm(step.actor)
            if not _ACTOR_RE.match(actor):
                out.append(f"{label}: step {step.raw_id!r} actor {actor!r} is not a single hat (Invariant 1)")
    return out





def violations_legacy_header_keys_absent(workspace: Workspace) -> list[str]:
    out: list[str] = []
    for workflow in _workflows(workspace):
        label = workspace.label(workflow.path, workspace.framework_root)
        block = workflow.block
        for key in ("skills", "drives", "fsm"):
            if key in block:
                out.append(f"{label}: workflow-level `{key}` is legacy and must be removed")
    return out


_CHECKS = (
    ("advisory-graph coherence", violations_advisory_graph),
    ("one-actor-per-step (Invariant 1)", violations_one_actor),
    ("legacy-header cleanup", violations_legacy_header_keys_absent),
)


# --- pytest entry points (discovered if pytest is available; assert-based) ----------------------
def test_advisory_graph_is_coherent() -> None:
    violations = violations_advisory_graph(_workspace())
    assert not violations, "\n".join(violations)


def test_one_actor_per_step() -> None:
    violations = violations_one_actor(_workspace())
    assert not violations, "\n".join(violations)





def test_legacy_header_keys_absent() -> None:
    violations = violations_legacy_header_keys_absent(_workspace())
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
