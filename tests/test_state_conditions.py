"""Tests for the two-CEL state-condition pipeline (set_query selects → set_predicate asserts).

Covers the corrected engine:
  - schema-driven CLOSED artifact views (exactly the schema's declared properties)
  - static invalidation of undeclared `<artifact>.<property>` references (query AND predicate)
  - the SELECTED set is bound as `selected` for the predicate (query result actually feeds it)
  - pass / fail / error surfaces end-to-end via evaluate_state

Run: python3 harness/tests/test_state_conditions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Artifact
from persistence import ArtifactRepository, SchemaRepository, Workspace
from services import CelEvaluator


def _epic(artifact_id: str, status: str = "done", **extra: object) -> Artifact:
    fields = {"id": artifact_id, "status": status, "type": "business", "title": artifact_id}
    fields.update(extra)
    return Artifact("epic", Path(f"/tmp/epics/{artifact_id}.md"), fields, "")


def _feature(artifact_id: str, status: str = "done", product: str = "p1", **extra: object) -> Artifact:
    fields = {"id": artifact_id, "status": status, "type": "business", "title": artifact_id}
    fields.update(extra)
    return Artifact("feature", Path(f"/tmp/{product}/features/{artifact_id}.md"), fields, "", product)


class _StubArtifactRepository(ArtifactRepository):
    """ArtifactRepository whose universe is injected programmatically (no filesystem scan)."""

    def __init__(self, workspace: Workspace, artifacts: list[Artifact]) -> None:
        super().__init__(workspace)
        self._universe = artifacts

    def discover(self) -> list[Artifact]:
        return self._universe


def _cel(artifacts: list[Artifact]) -> CelEvaluator:
    ws = Workspace.detect()
    repo = _StubArtifactRepository(ws, artifacts)
    schemas = SchemaRepository(ws)
    return CelEvaluator(ws, repo, None, None, schemas)


def _sel(*bindings: tuple[str, str], query: str) -> dict:
    return {
        "set_type": "artifact",
        "artifact_types": [{"alias": a, "schema_id": s} for a, s in bindings],
        "set_query": query,
    }


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ok  {name}")
        else:
            print(f"  FAIL {name} {detail}")
            failures.append(f"{name} {detail}".strip())

    # (a) alias/schema resolution: known schema ok, unknown schema errors
    print("(a) alias + schema resolution")
    cel = _cel([_epic("E-1")])
    props, err = cel._alias_props([{"alias": "epic", "schema_id": "epic"}])
    check("known schema resolves", err is None and props is not None and "status" in props["epic"])
    _, err = cel._alias_props([{"alias": "x", "schema_id": "not-a-schema"}])
    check("unknown schema_id errors", err is not None and "unknown schema_id" in err)

    # (b) closed artifact view: exactly the schema's declared props, no __-injected, no off-schema
    print("(b) closed artifact view")
    props, _ = cel._alias_props([{"alias": "epic", "schema_id": "epic"}])
    view = cel._artifact_view(_epic("E-9", strategic_theme="growth"), props["epic"])
    check("declared prop present", "status" in view and view["status"] == "done")
    check("authored extra prop present", view.get("strategic_theme") == "growth")
    check("harness __ prop excluded", "__path" not in view and "__kind" not in view)
    check("off-schema key absent", "bogus" not in view)

    # (c) static field validation against schema (the actual invalidation mechanism)
    print("(c) static field-reference validation")
    ap = {"epic": props["epic"]}
    check("declared prop in comprehension ok", cel._validate_field_refs("epic.all(e, e.status == 'done')", ap) is None)
    check("declared prop direct-filter ok", cel._validate_field_refs("epic.filter(e, e.strategic_theme != '')", ap) is None)
    bad = cel._validate_field_refs("epic.all(e, e.nonexistent > 0)", ap)
    check("undeclared prop invalidated", bad is not None and "nonexistent" in bad, f"got {bad!r}")
    check("nested filter+all resolves alias", cel._validate_field_refs("epic.filter(e, e.status=='done').all(x, x.title != '')", ap) is None)
    bad2 = cel._validate_field_refs("epic.all(e, e.status=='done' && e.wrongfield==1)", ap)
    check("undeclared prop in compound invalidated", bad2 is not None and "wrongfield" in bad2)
    check("runtime constant not treated as artifact prop", cel._validate_field_refs("size(epic) > 0 && product == null", ap) is None)

    # (d) evaluate_state PASS: select all epics, assert every selected is done
    print("(d) evaluate_state — pass")
    cel = _cel([_epic("E-1", "done"), _epic("E-2", "done")])
    outcome, detail = cel.evaluate_state(_sel(("epic", "epic"), query="epic"), "selected.all(e, e.status == 'done')")
    check("all-done asserts pass", outcome == "pass", f"got {outcome}: {detail}")
    check("detail reports selection size", "selected 2 of 2" in detail, f"got {detail}")

    # query actually feeds the predicate: filter to the single done epic, assert size(selected)==1
    cel = _cel([_epic("E-1", "done"), _epic("E-2", "blocked"), _epic("E-3", "blocked")])
    outcome, detail = cel.evaluate_state(
        _sel(("epic", "epic"), query="epic.filter(e, e.status == 'done')"),
        "size(selected) == 1",
    )
    check("query result feeds predicate (selected)", outcome == "pass", f"got {outcome}: {detail}")
    check("detail reports filtered selection", "selected 1 of 3" in detail, f"got {detail}")

    # (e) evaluate_state FAIL: assert all epics done when one is blocked
    print("(e) evaluate_state — fail")
    outcome, detail = cel.evaluate_state(_sel(("epic", "epic"), query="epic"), "selected.all(e, e.status == 'done')")
    check("mixed-status asserts fail", outcome == "fail", f"got {outcome}: {detail}")

    # (f) error surfaces
    print("(f) error surfaces")
    # unknown schema_id
    outcome, detail = cel.evaluate_state(_sel(("x", "nope"), query="x"), "size(selected) > 0")
    check("unknown schema_id → error", outcome == "error" and "unknown schema_id" in detail, f"got {outcome}: {detail}")
    # undeclared property in set_query → error (static)
    outcome, detail = cel.evaluate_state(_sel(("epic", "epic"), query="epic.filter(e, e.ghost == 1)"), "size(selected) > 0")
    check("undeclared prop in query → error", outcome == "error" and "set_query" in detail and "ghost" in detail, f"got {outcome}: {detail}")
    # undeclared property in set_predicate → error (static)
    outcome, detail = cel.evaluate_state(_sel(("epic", "epic"), query="epic"), "selected.all(e, e.ghost == 1)")
    check("undeclared prop in predicate → error", outcome == "error" and "set_predicate" in detail and "ghost" in detail, f"got {outcome}: {detail}")
    # set_query yields a scalar, not a set → error
    outcome, detail = cel.evaluate_state(_sel(("epic", "epic"), query="size(epic)"), "size(selected) > 0")
    check("scalar query result → error", outcome == "error" and "set" in detail.lower(), f"got {outcome}: {detail}")
    # malformed CEL in query → error
    outcome, detail = cel.evaluate_state(_sel(("epic", "epic"), query="epic..broken"), "size(selected) > 0")
    check("malformed query CEL → error", outcome == "error", f"got {outcome}: {detail}")
    # missing predicate → error
    outcome, detail = cel.evaluate_state(_sel(("epic", "epic"), query="epic"), None)
    check("missing predicate → error", outcome == "error", f"got {outcome}: {detail}")

    # (g) multi-alias FROM: two schemas bound, predicate over selected subset
    print("(g) multi-alias selection")
    cel = _cel([_epic("E-1", "done"), _feature("F-1", "done"), _feature("F-2", "blocked")])
    outcome, detail = cel.evaluate_state(
        _sel(("epic", "epic"), ("feature", "feature"), query="feature.filter(f, f.status == 'blocked')"),
        "selected.all(f, f.status == 'blocked')",
    )
    check("multi-alias query + predicate pass", outcome == "pass", f"got {outcome}: {detail}")
    check("multi-alias detail counts full universe", "of 3" in detail, f"got {detail}")

    if failures:
        print(f"\n{len(failures)} test violation(s)")
        return 1
    print("\npass: 0 test violation(s)")
    return 0


def test_state_conditions() -> None:
    """pytest entry."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
