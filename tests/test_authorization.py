"""Authorization gate TESTS — privilege-based write-authority over a run log (harness ACL).

DESIGN-TIME framework tests for the ``AuthorizationChecker`` + ``AuthorizationPolicy`` + ``acl/map.yaml``:
a write is legal only when the actor holds a covering ``<action>_<resource>`` privilege. Asserts the
original drift (RTE writing the portfolio singleton) is rejected; VMO passes; RTE may update_feature.status
but not rewrite a PM-owned Feature; PM may; a missing actor is flagged. Run: ``python3 harness/tests/test_authorization.py``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mappers import LogRepository, SchemaRepository, Workspace
from services import AuthorizationChecker, AuthorizationPolicy


def _check(lines: list[dict]) -> tuple[int, int]:
    workspace = Workspace.detect()
    checker = AuthorizationChecker(workspace, SchemaRepository(workspace), LogRepository(workspace), AuthorizationPolicy())
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")
        path = Path(handle.name)
    report = checker.check_log(path)
    errors = sum(1 for f in report.findings if f.severity == "error")
    warnings = sum(1 for f in report.findings if f.severity == "warning")
    return errors, warnings


def main() -> int:
    failures: list[str] = []

    # 1. RTE writing the portfolio singleton is ungranted (the original drift).
    errors, _ = _check([{"actor": "release-train-engineer", "action": "update", "outputs": ["portfolio/portfolio.yaml"]}])
    if errors != 1:
        failures.append(f"RTE→portfolio.yaml should error once, got {errors}")

    # 2. VMO writing the singleton is granted.
    errors, _ = _check([{"actor": "value-management-officier", "action": "update", "outputs": ["portfolio/portfolio.yaml"]}])
    if errors != 0:
        failures.append(f"VMO→portfolio.yaml should pass, got {errors} error(s)")

    # 3. A product manifest is VMO-only too.
    errors, _ = _check([{"actor": "scrum-master", "action": "update", "outputs": ["portfolio/foo/product.yaml"]}])
    if errors != 1:
        failures.append(f"SM→product.yaml should error once, got {errors}")

    # 4. Property-level is gone: RTE editing a Feature (even just its status field) is rejected —
    #    Feature content is PM's; RTE flips status via the transition plane + the program kanban.
    errors, _ = _check([{"actor": "release-train-engineer", "action": "update", "outputs": ["portfolio/p/features/F-01.md#status"]}])
    if errors != 1:
        failures.append(f"RTE→feature.status should error once (property-level dropped), got {errors}")

    # 5. RTE may NOT rewrite the whole Feature (kind-level update is PM's).
    errors, _ = _check([{"actor": "release-train-engineer", "action": "update", "outputs": ["portfolio/p/features/F-01.md"]}])
    if errors != 1:
        failures.append(f"RTE→feature (whole) should error once, got {errors}")

    # 6. PM may rewrite the whole Feature.
    errors, _ = _check([{"actor": "product-manager", "action": "update", "outputs": ["portfolio/p/features/F-01.md"]}])
    if errors != 0:
        failures.append(f"PM→feature should pass, got {errors} error(s)")

    # 7. Missing actor on a write is flagged.
    _, warnings = _check([{"outputs": ["portfolio/portfolio.yaml"]}])
    if warnings != 1:
        failures.append(f"missing actor should warn once, got {warnings}")

    # 8/9. Business vs enabler distinction is by frontmatter `type`. An enabler Feature is the
    #      System-Architect's resource, not the PM's. Write a real enabler artifact under the
    #      workspace so the checker can read its type, assert PM is rejected + SA passes, clean up.
    workspace = Workspace.detect()
    enabler = workspace.portfolio_root / "_acltest" / "features" / "F-EN.md"
    enabler.parent.mkdir(parents=True, exist_ok=True)
    enabler.write_text("---\ntype: enabler\n---\n", encoding="utf-8")
    rel = "portfolio/_acltest/features/F-EN.md"
    try:
        errors, _ = _check([{"actor": "product-manager", "action": "update", "outputs": [rel]}])
        if errors != 1:
            failures.append(f"PM→enabler feature should error once, got {errors}")
        errors, _ = _check([{"actor": "system-architect", "action": "update", "outputs": [rel]}])
        if errors != 0:
            failures.append(f"SA→enabler feature should pass, got {errors} error(s)")
    finally:
        enabler.unlink(missing_ok=True)
        enabler.parent.rmdir()
        enabler.parent.parent.rmdir()

    if failures:
        for f in failures:
            print(f"FAIL  {f}")
        print(f"\n{len(failures)} authorization violation(s)")
        return 1
    print("pass  RTE singleton write rejected")
    print("pass  VMO singleton write granted")
    print("pass  product manifest VMO-only")
    print("pass  RTE feature.status edit rejected (plain RBAC)")
    print("pass  RTE full feature rewrite rejected")
    print("pass  PM full feature rewrite granted")
    print("pass  missing-actor write flagged")
    print("pass  PM enabler feature rewrite rejected")
    print("pass  SA enabler feature rewrite granted")
    print("\npass: 0 authorization violation(s)")
    return 0


def test_authorization_acl() -> None:
    """pytest entry: the full ACL suite must report zero violations."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())

