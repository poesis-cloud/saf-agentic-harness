"""Hook adapter TESTS — the environment-hook funnel routes through the same authorization plane.

DESIGN-TIME tests for ``HookService``: preToolUse on a write derives (actor, action, outputs) and
denies an ungranted write (RTE rewriting a PM Feature) while allowing a granted one (PM); a read-only
tool is never blocked; an enabler write resolves to the enabler resource. Run:
``python3 harness/tests/test_hooks.py``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persistence import LogRepository, SchemaRepository, Workspace
from services import AuthorizationPolicy, HookService


def _svc() -> HookService:
    ws = Workspace.detect()
    return HookService(ws, SchemaRepository(ws), LogRepository(ws), AuthorizationPolicy())


def main() -> int:
    svc = _svc()
    failures: list[str] = []

    # 1. RTE rewriting a whole PM-owned Feature via an edit tool is denied (RTE may only status-flip).
    d = svc.handle("preToolUse", {"agent": "release-train-engineer", "tool": "replace_string_in_file", "tool_input": {"filePath": "portfolio/p/features/F-01.md"}})
    if d.permission != "deny":
        failures.append(f"RTE feature rewrite should deny, got {d.permission}")

    # 2. PM writing the Feature is allowed.
    d = svc.handle("preToolUse", {"agent": "product-manager", "tool": "create_file", "tool_input": {"filePath": "portfolio/p/features/F-01.md"}})
    if d.permission != "allow":
        failures.append(f"PM feature create should allow, got {d.permission}")

    # 3. Property-level is gone: an RTE edit to a Feature (even a status-only path) is denied —
    #    plain RBAC authorizes the whole resource (Feature = PM's); status flows via transition/kanban.
    d = svc.handle("preToolUse", {"agent": "release-train-engineer", "tool": "replace_string_in_file", "tool_input": {"filePath": "portfolio/p/features/F-01.md#status"}})
    if d.permission != "deny":
        failures.append(f"RTE feature.status edit should deny (property-level dropped), got {d.permission}")

    # 4. A read-only tool is never blocked, regardless of actor.
    d = svc.handle("preToolUse", {"agent": "release-train-engineer", "tool": "read_file", "tool_input": {"filePath": "portfolio/portfolio.yaml"}})
    if d.permission != "allow":
        failures.append(f"read_file should never block, got {d.permission}")

    # 5. The phase→command map auto-runs only hook-feasible (write-scope) commands; unit-scope is excluded.
    #    postToolUse schema-checks the written artifact; session-close runs NO env sweep (the redundant
    #    full backlog re-sweep is dropped — review_session re-checks the recorded steps' postconditions).
    if "check-artifact" not in svc.commands_for("postcondition"):
        failures.append("postcondition should auto-run check-artifact on writes")
    if svc.commands_for("session-close"):
        failures.append("session-close should not auto-run an env sweep (redundant full re-sweep dropped)")
    if "check-step" in svc.commands_for("postcondition"):
        failures.append("unit-scope check-step must not auto-run at a boundary")

    # 6. sessionStart injects orchestrator context + invariants as additionalContext,
    #    including the suborchestration skill map (sub-id -> procedure skill) for the active root.
    d = svc.handle("sessionStart", {"agent": "release-train-engineer"})
    if "orchestration art: facilitate @release-train-engineer" not in d.context:
        failures.append("sessionStart should inject the RTE orchestrator context")
    if "feature-backlog-refinement" not in d.context:
        failures.append("sessionStart should inject the RTE suborchestration skill map")

    # 7. Dispatch governance (preToolUse on runSubagent): the (target agent, model) selection is gated
    #    against the routing map — Auto/omitted/unknown/below-floor models deny; a valid in-floor one allows.
    def dispatch(agent: str, model: str | None):
        tool_input: dict[str, object] = {"agentName": agent}
        if model is not None:
            tool_input["model"] = model
        return svc.handle("preToolUse", {"agent": "release-train-engineer", "tool": "runSubagent", "tool_input": tool_input})

    if dispatch("developer", "Auto").permission != "deny":
        failures.append("runSubagent with model=Auto should deny")
    if dispatch("developer", None).permission != "deny":
        failures.append("runSubagent with omitted model should deny")
    if dispatch("developer", "Imaginary Model (copilot)").permission != "deny":
        failures.append("runSubagent with an unknown model should deny")
    if dispatch("security-expert", "GPT-5.4 mini (copilot)").permission != "deny":
        failures.append("runSubagent below the role-default floor (security-expert < balanced) should deny")
    if dispatch("developer", "GPT-5.4 mini (copilot)").permission != "allow":
        failures.append("runSubagent with a valid in-floor model should allow")

    # 8. A dispatched CHILD step session inherits ITS step's context (Option B — correlate-by-actor
    #    via the run journal's open `dispatch`): given an orchestrate dispatch to @business-owner for
    #    the epic-lean-business-case/discovery-and-draft step, that actor's sessionStart injects the
    #    step's procedure skill (per-step injection), NOT the orchestrator's root+sub map.
    with tempfile.TemporaryDirectory() as tmp:
        ws2 = Workspace.detect(portfolio_root=Path(tmp))
        svc2 = HookService(ws2, SchemaRepository(ws2), LogRepository(ws2), AuthorizationPolicy())
        LogRepository(ws2).append_entry(
            ws2.run_journal("R-child"), command="orchestrate",
            payload={"action": "dispatch", "step": "discovery-and-draft"},
            run="R-child", orchestration="epic-lean-business-case", step="discovery-and-draft",
            unit="sie-observability-foundation", actor="business-owner", status="dispatch")
        child = svc2.handle("sessionStart", {"agent": "business-owner"})
        if "epic-lean-business-case" not in child.context or "discovery-and-draft" not in child.context:
            failures.append(f"child sessionStart should inherit its step skill, got {child.context!r}")
        if "facilitate" in child.context:
            failures.append("a child step session must not get the orchestrator root map")
        # an actor with no open dispatch falls back to the orchestrator map (root facilitator).
        orch = svc2.handle("sessionStart", {"agent": "value-management-officier"})
        if "facilitate @value-management-officier" not in orch.context:
            failures.append("an un-dispatched root facilitator should get the orchestrator map")

    if failures:
        for f in failures:
            print(f"FAIL  {f}")
        print(f"\n{len(failures)} hook violation(s)")
        return 1
    print("pass  RTE feature rewrite denied")
    print("pass  PM feature write allowed")
    print("pass  RTE feature.status edit denied (plain RBAC)")
    print("pass  read-only tool never blocked")
    print("pass  map auto-runs env/write commands, excludes unit-scope")
    print("pass  sessionStart injects workflow + suborchestration skills map")
    print("pass  dispatch governance gates agent+model selection")
    print("pass  child step session inherits its dispatched step skill (per-step injection)")
    print("\npass: 0 hook violation(s)")
    return 0


def test_hook_authorization_funnel() -> None:
    """pytest entry: the hook funnel suite must report zero violations."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
