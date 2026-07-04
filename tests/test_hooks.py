"""Hook adapter TESTS — the environment-hook funnel routes through the same authorization plane.

DESIGN-TIME tests for ``HookService``: preToolUse on a write derives (actor, action, outputs) and
denies an ungranted write while allowing a granted one; a read-only tool is never blocked; an enabler
write resolves to the enabler resource. All actors, paths, workflows, and model keys are loaded from
the framework config/schema catalog so the suite stays methodology-agnostic. Run:
``python3 harness/tests/test_hooks.py``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import FrameworkConfig, SchemaCatalog
from mappers import LogMapper, Workspace
from services import AuthorizationPolicy, HookService, ModelRouter


def _schema_path_example(schemas: dict[str, dict], schema_id: str, variables: dict[str, str]) -> str | None:
    """Return a concrete repo-relative path for *schema_id* by substituting template tokens and
    collapsing glob wildcards to the supplied variable values (or sensible defaults)."""
    schema = schemas.get(schema_id)
    if not schema:
        return None
    patterns = schema.get("x-artifact", {}).get("pathPatterns") or []
    if not isinstance(patterns, list):
        patterns = [patterns]
    for pattern in patterns:
        candidate = pattern
        for key, value in variables.items():
            candidate = candidate.replace(f"{{{key}}}", value)
        parts = candidate.split("/")
        for i, part in enumerate(parts):
            if "*" in part:
                if i == 0:
                    parts[i] = part.replace("*", variables.get("product", "p"))
                else:
                    parts[i] = part.replace("*", variables.get("unit_id", "item-01"))
        candidate = "/".join(parts)
        if "{" not in candidate and "*" not in candidate:
            return candidate
    return None


def _agents_with_privilege(policy: AuthorizationPolicy, action: str, resource: str) -> list[str]:
    return [agent for agent, privs in policy.agents().items() if policy.allows(agent, action, resource)]


def _agents_without_privilege(policy: AuthorizationPolicy, action: str, resource: str) -> list[str]:
    return [agent for agent, privs in policy.agents().items() if not policy.allows(agent, action, resource)]


SINGLETON_PATH_KIND = {
    "portfolio-manifest.yaml": "portfolio-manifest",
    "_registry.yaml": "portfolio-manifest",
    "strategic-themes.md": "strategic-themes",
    "portfolio-vision.md": "portfolio-vision",
    "portfolio-roadmap.md": "portfolio-roadmap",
    "art/*/art-manifest.yaml": "art-manifest",
    "art/*/teams/*/team-manifest.yaml": "team-manifest",
    "products/*/product-manifest.yaml": "product-manifest",
}


def _svc(workspace: Workspace | None = None) -> HookService:
    ws = workspace or Workspace.detect()
    cfg = FrameworkConfig.detect(ws)
    return HookService(ws, SchemaCatalog(ws), LogMapper(ws), AuthorizationPolicy(cfg.access_control_list, singleton_path_kind=SINGLETON_PATH_KIND), cfg.workflows, ModelRouter(cfg.model_profiles))


def main() -> int:
    workspace = Workspace.detect()
    cfg = FrameworkConfig.detect(workspace)
    policy = AuthorizationPolicy(cfg.access_control_list)
    schemas = SchemaCatalog(workspace).load_raw()
    workflows = cfg.workflows.all()
    router = ModelRouter(cfg.model_profiles)
    svc = _svc(workspace)
    failures: list[str] = []

    # Discover a non-singleton artifact path from the schema catalog.
    artifact_path = _schema_path_example(schemas, "story", {"product": "p", "unit_id": "S-01"})
    if artifact_path is None:
        failures.append("could not resolve a concrete path for schema 'story'")
        return 1

    artifact_owners = _agents_with_privilege(policy, "update", "story")
    artifact_non_owners = _agents_without_privilege(policy, "update", "story")
    if not artifact_owners:
        failures.append("no agent granted update_story")
    if not artifact_non_owners:
        failures.append("no agent lacks update_story")

    # 1. Non-owner rewriting a whole artifact via an edit tool is denied.
    if artifact_non_owners:
        d = svc.handle("preToolUse", {"agent": artifact_non_owners[0], "tool": "replace_string_in_file", "tool_input": {"filePath": artifact_path}})
        if d.permission != "deny":
            failures.append(f"{artifact_non_owners[0]} artifact rewrite should deny, got {d.permission}")

    # 2. Owner writing the artifact is allowed.
    if artifact_owners:
        d = svc.handle("preToolUse", {"agent": artifact_owners[0], "tool": "create_file", "tool_input": {"filePath": artifact_path}})
        if d.permission != "allow":
            failures.append(f"{artifact_owners[0]} artifact create should allow, got {d.permission}")

    # 3. Property-level is gone: even a #status suffix is denied for non-owners.
    if artifact_non_owners:
        d = svc.handle("preToolUse", {"agent": artifact_non_owners[0], "tool": "replace_string_in_file", "tool_input": {"filePath": f"{artifact_path}#status"}})
        if d.permission != "deny":
            failures.append(f"{artifact_non_owners[0]} artifact.status edit should deny (property-level dropped), got {d.permission}")

    # 4. A read-only tool is never blocked, regardless of actor.
    singleton_path = next((p for p in SINGLETON_PATH_KIND if "." not in p), "portfolio-manifest.yaml")
    d = svc.handle("preToolUse", {"agent": artifact_non_owners[0] if artifact_non_owners else "unknown", "tool": "read_file", "tool_input": {"filePath": singleton_path}})
    if d.permission != "allow":
        failures.append(f"read_file should never block, got {d.permission}")

    # 5. The phase→command map auto-runs only hook-feasible (write-scope) commands; unit-scope is excluded.
    if "check-artifact" not in svc.commands_for("postcondition"):
        failures.append("postcondition should auto-run check-artifact on writes")
    if svc.commands_for("session-close"):
        failures.append("session-close should not auto-run an env sweep (redundant full re-sweep dropped)")
    if "check-step" in svc.commands_for("postcondition"):
        failures.append("unit-scope check-step must not auto-run at a boundary")

    # 6. sessionStart injects orchestrator context + invariants as additionalContext,
    #    including the suborchestration skill map (sub-id -> procedure skill) for the active root.
    roots = [w for w in workflows if getattr(w, "is_root", False)]
    if not roots:
        failures.append("no root workflow found")
    else:
        root = roots[0]
        facilitator = str(root.facilitator).lstrip("@") if root.facilitator else ""
        d = svc.handle("sessionStart", {"agent": facilitator})
        if f"orchestration {root.id}: facilitate @{facilitator}" not in d.context:
            failures.append(f"sessionStart should inject the {facilitator} orchestrator context")
        subs = [w for w in workflows if getattr(w, "parent", None) == root.id]
        if subs:
            sub_id = str(subs[0].id)
            if f"on suborchestration {sub_id}" not in d.context:
                failures.append(f"sessionStart should inject the {sub_id} suborchestration skill map")

    # 7. Dispatch governance (preToolUse on runSubagent): the (target agent, model) selection is
    #    gated against the model catalog — Auto/omitted/unknown models deny; a known catalog id allows.
    models = router.profiles.models()
    if not models:
        failures.append("conf/model-profiles.conf.yaml catalog is empty — dispatch governance cannot be exercised")
    else:
        known_model = next(iter(models.keys()))
        unknown_model = "Imaginary Model (copilot)"
        if unknown_model in models:
            unknown_model = "Totally Unknown Model (copilot)"

        def dispatch(agent: str, model: str | None):
            tool_input: dict[str, object] = {"agentName": agent}
            if model is not None:
                tool_input["model"] = model
            return svc.handle("preToolUse", {"agent": facilitator or "orchestrator", "tool": "runSubagent", "tool_input": tool_input})

        if dispatch("developer", "Auto").permission != "deny":
            failures.append("runSubagent with model=Auto should deny")
        if dispatch("developer", None).permission != "deny":
            failures.append("runSubagent with omitted model should deny")
        if dispatch("developer", unknown_model).permission != "deny":
            failures.append("runSubagent with an unknown model should deny")
        if dispatch("developer", known_model).permission != "allow":
            failures.append("runSubagent with a known catalog model should allow")

    # 8. A dispatched CHILD step session inherits ITS step's context (Option B — correlate-by-actor
    #    via the run journal's open `dispatch`): given an orchestrate dispatch to a step actor, that
    #    actor's sessionStart injects the step's procedure skill (per-step injection), NOT the
    #    orchestrator's root+sub map. Discover a sub-workflow step that carries instructions/prompts
    #    or delegates to another workflow so _inject_step produces context. Pick a sub whose
    #    facilitator differs from the root facilitator so the root-facilitator session has no open
    #    dispatch and falls back to the orchestrator map.
    child_frame: tuple[Any, Any, str] | None = None
    root = roots[0] if roots else None
    root_facilitator = str(root.facilitator).lstrip("@") if root and root.facilitator else ""
    for sub in (w for w in workflows if getattr(w, "parent", None) and hasattr(w, "steps")):
        sub_facilitator = str(sub.facilitator).lstrip("@") if sub.facilitator else ""
        if sub_facilitator == root_facilitator:
            continue
        for s in sub.steps:
            has_guidance = bool(getattr(s, "instructions", None) or getattr(s, "prompts", None))
            has_skill = bool(getattr(s, "skills", None) or getattr(s, "delegates_to", None))
            if has_guidance or has_skill:
                child_frame = (sub, s, str(s.id))
                break
        if child_frame:
            break

    if child_frame is None:
        failures.append("no sub-workflow step with instructions/prompts/skills found for a different facilitator")
    elif root:
        sub, step, step_id = child_frame
        unit_id = "test-unit-01"
        with tempfile.TemporaryDirectory() as tmp:
            ws2 = Workspace.detect(workspace_root=Path(tmp))
            cfg2 = FrameworkConfig.detect(ws2)
            svc2 = HookService(ws2, SchemaCatalog(ws2), LogMapper(ws2), AuthorizationPolicy(cfg2.access_control_list), cfg2.workflows, ModelRouter(cfg2.model_profiles))
            step_actor = str(sub.facilitator).lstrip("@") if sub.facilitator else "step-actor"
            LogMapper(ws2).append_entry(
                ws2.run_journal("R-child"), command="orchestrate",
                payload={"action": "dispatch", "step": step_id},
                run="R-child", orchestration=str(sub.id), step=step_id,
                unit=unit_id, actor=step_actor, status="dispatch")
            child = svc2.handle("sessionStart", {"agent": step_actor})
            if str(sub.id) not in child.context or step_id not in child.context:
                failures.append(f"child sessionStart should inherit its step skill, got {child.context!r}")
            if "facilitate" in child.context:
                failures.append("a child step session must not get the orchestrator root map")
            # an actor with no open dispatch falls back to the orchestrator map (root facilitator).
            orch = svc2.handle("sessionStart", {"agent": root_facilitator})
            if f"orchestration {root.id}: facilitate @{root_facilitator}" not in orch.context:
                failures.append("an un-dispatched root facilitator should get the orchestrator map")

    if failures:
        for f in failures:
            print(f"FAIL  {f}")
        print(f"\n{len(failures)} hook violation(s)")
        return 1
    print("pass  non-owner artifact rewrite denied")
    print("pass  owner artifact write allowed")
    print("pass  non-owner artifact.status edit denied (plain RBAC)")
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
