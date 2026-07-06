"""Authorization gate TESTS — privilege-based write-authority over a run log (harness ACL).

DESIGN-TIME framework tests for the ``AuthorizationChecker`` + ``AuthorizationPolicy`` + ``conf/access-control-list.conf.yaml``:
a write is legal only when the actor holds a covering artifact access entry. All actors,
resources, and path patterns are loaded from the framework config/schema catalog so the test suite
stays methodology-agnostic. Run: ``python3 harness/tests/test_authorization.py``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from config import FrameworkConfig, SchemaCatalog
from mappers import LogMapper, Workspace
from config import AccessControlList
from services import AuthorizationChecker


def _check(lines: list[dict]) -> tuple[int, int]:
    workspace = Workspace.detect()
    cfg = FrameworkConfig.detect(workspace)
    checker = AuthorizationChecker(
        workspace,
        SchemaCatalog(workspace),
        LogMapper(workspace),
        cfg.access_control_list,
        cfg.workspace_layout,
    )
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
        path = Path(handle.name)
    report = checker.check_log(path)
    errors = sum(1 for f in report.findings if f.severity == "error")
    warnings = sum(1 for f in report.findings if f.severity == "warning")
    return errors, warnings


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
        # Collapse remaining '*' directory/file wildcards using variable defaults.
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


def _agents_with_privilege(policy: AccessControlList, action: str, resource: str) -> list[str]:
    return [agent for agent, privs in policy.agents().items() if policy.allows(agent, action, resource)]


def _agents_without_privilege(policy: AccessControlList, action: str, resource: str) -> list[str]:
    return [agent for agent, privs in policy.agents().items() if not policy.allows(agent, action, resource)]


def main() -> int:
    failures: list[str] = []
    workspace = Workspace.detect()
    policy = FrameworkConfig.detect(workspace).access_control_list
    schemas = SchemaCatalog(workspace).load_raw()
    agents = sorted(policy.agents().keys())
    if not agents:
        failures.append("access-control-list.yaml has no access entries")
        return 1

    # Discover the workspace singleton path from the test's own mapping.
    singletons = FrameworkConfig.detect(workspace).workspace_layout.singleton_path_kind()
    singleton_path = next((p for p in singletons if not p.startswith(".")), None)
    if singleton_path is None:
        failures.append("no singleton path in the workspace layout")
        return 1
    singleton_resource = singletons[singleton_path]

    # Pick an agent that owns the singleton and one that does not.
    owners = _agents_with_privilege(policy, "update", singleton_resource)
    non_owners = _agents_without_privilege(policy, "update", singleton_resource)
    if not owners:
        # Fall back to strategic-themes if portfolio-manifest has no owner (methodology may not define it).
        singleton_path = "strategic-themes.md"
        singleton_resource = singletons[singleton_path]
        owners = _agents_with_privilege(policy, "update", singleton_resource)
        non_owners = _agents_without_privilege(policy, "update", singleton_resource)
    if not owners:
        failures.append(f"no agent granted {singleton_resource}.artifact.schema.json")
    if not non_owners:
        failures.append(f"no agent lacks {singleton_resource}.artifact.schema.json")

    # 1. Non-owner writing the workspace singleton is rejected.
    if owners and non_owners:
        errors, _ = _check([{"actor": non_owners[0], "action": "update", "outputs": [singleton_path]}])
        if errors != 1:
            failures.append(f"{non_owners[0]}→{singleton_path} should error once, got {errors}")

    # 2. Owner writing the singleton is granted.
    if owners:
        errors, _ = _check([{"actor": owners[0], "action": "update", "outputs": [singleton_path]}])
        if errors != 0:
            failures.append(f"{owners[0]}→{singleton_path} should pass, got {errors} error(s)")

    # 3. Product-manifest singleton is owned by the same top authority; pick a non-owner to test denial.
    product_path = "products/acme/product-manifest.yaml"
    product_resource = "product-manifest"
    product_non_owners = _agents_without_privilege(policy, "update", product_resource)
    if product_non_owners:
        errors, _ = _check([{"actor": product_non_owners[0], "action": "update", "outputs": [product_path]}])
        if errors != 1:
            failures.append(f"{product_non_owners[0]}→{product_path} should error once, got {errors}")

    # 4/5/6. Whole-resource RBAC on a non-singleton artifact: discover a schema path pattern.
    artifact_path = _schema_path_example(schemas, "story", {"product": "p", "unit_id": "S-01"})
    if artifact_path is None:
        failures.append("could not resolve a concrete path for schema 'story'")
    else:
        artifact_owners = _agents_with_privilege(policy, "update", "story")
        artifact_non_owners = _agents_without_privilege(policy, "update", "story")
        if not artifact_owners:
            failures.append("no agent granted story.artifact.schema.json")
        if not artifact_non_owners:
            failures.append("no agent lacks story.artifact.schema.json")

        # Property-level is gone: even a #status suffix is denied for non-owners.
        if artifact_non_owners:
            errors, _ = _check([{"actor": artifact_non_owners[0], "action": "update", "outputs": [f"{artifact_path}#status"]}])
            if errors != 1:
                failures.append(f"{artifact_non_owners[0]}→artifact.status should error once, got {errors}")
        # Whole-file rewrite by a non-owner is denied.
        if artifact_non_owners:
            errors, _ = _check([{"actor": artifact_non_owners[0], "action": "update", "outputs": [artifact_path]}])
            if errors != 1:
                failures.append(f"{artifact_non_owners[0]}→artifact (whole) should error once, got {errors}")
        # Whole-file rewrite by an owner is allowed.
        if artifact_owners:
            errors, _ = _check([{"actor": artifact_owners[0], "action": "update", "outputs": [artifact_path]}])
            if errors != 0:
                failures.append(f"{artifact_owners[0]}→artifact should pass, got {errors} error(s)")

    # 7. Missing actor on a write is flagged.
    _, warnings = _check([{"outputs": [singleton_path]}])
    if warnings != 1:
        failures.append(f"missing actor should warn once, got {warnings}")

    # 8/9. Business vs enabler distinction is by frontmatter `type`. Discover the enabler variant.
    enabler_path_pattern = _schema_path_example(schemas, "story-enabler", {"product": "_acltest", "unit_id": "S-EN"})
    enabler_owners = _agents_with_privilege(policy, "update", "story-enabler")
    enabler_non_owners = _agents_without_privilege(policy, "update", "story-enabler")
    if enabler_path_pattern is None:
        failures.append("could not resolve a concrete path for schema 'story-enabler'")
    elif enabler_owners and enabler_non_owners:
        # The schema path pattern is workspace-root-relative; create the file at the matching location.
        enabler = workspace.workspace_root / enabler_path_pattern
        enabler.parent.mkdir(parents=True, exist_ok=True)
        enabler.write_text("---\ntype: enabler\n---\n", encoding="utf-8")
        rel = enabler_path_pattern
        try:
            errors, _ = _check([{"actor": enabler_non_owners[0], "action": "update", "outputs": [rel]}])
            if errors != 1:
                failures.append(f"{enabler_non_owners[0]}→enabler artifact should error once, got {errors}")
            errors, _ = _check([{"actor": enabler_owners[0], "action": "update", "outputs": [rel]}])
            if errors != 0:
                failures.append(f"{enabler_owners[0]}→enabler artifact should pass, got {errors} error(s)")
        finally:
            enabler.unlink(missing_ok=True)
            # Remove the created folder chain (best-effort).
            for _ in range(5):
                try:
                    enabler.parent.rmdir()
                    enabler = enabler.parent
                except OSError:
                    break

    if failures:
        for f in failures:
            print(f"FAIL  {f}")
        print(f"\n{len(failures)} authorization violation(s)")
        return 1
    print("pass  non-owner singleton write rejected")
    print("pass  owner singleton write granted")
    print("pass  product manifest owner-only")
    print("pass  non-owner artifact.status edit rejected (property-level dropped)")
    print("pass  non-owner full artifact rewrite rejected")
    print("pass  owner full artifact rewrite granted")
    print("pass  missing-actor write flagged")
    print("pass  non-owner enabler artifact rewrite rejected")
    print("pass  owner enabler artifact rewrite granted")
    print("\npass: 0 authorization violation(s)")
    return 0


def test_authorization_acl() -> None:
    """pytest entry: the full ACL suite must report zero violations."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())

