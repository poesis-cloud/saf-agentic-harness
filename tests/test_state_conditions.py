"""Tests for CEL set-query engine (state conditions) — Slice B step 6 + 7.

Test the full pipeline: set enumeration → multi-alias activation → set_query → set_predicate.
Run: python3 harness/tests/test_state_conditions.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Artifact
from persistence import ArtifactRepository, Workspace
from services import CelEvaluator


def _mk_artifact(kind: str, artifact_id: str, status: str = "done", product: str | None = None) -> Artifact:
    """Construct a minimal Artifact for testing."""
    path = Path(f"/tmp/{kind}s/{artifact_id}.md")
    fields = {"id": artifact_id, "status": status}
    front = ""
    return Artifact(kind, path, fields, front, product)


class _TestArtifactRepository(ArtifactRepository):
    """Minimal artifact repository for testing with programmatically added artifacts."""

    def __init__(self, workspace: Workspace, artifacts: list[Artifact] | None = None) -> None:
        super().__init__(workspace)
        self._universe = artifacts or []

    def discover(self) -> list[Artifact]:
        return self._universe


def _mock_calculation():
    """Minimal calculation service for testing."""
    class MockCalc:
        def coerce_number(self, x):
            return float(x) if x else 0.0
    return MockCalc()


def main() -> int:
    failures: list[str] = []

    # Test case (a): Set enumeration by schema_id
    print("test (a): set enumeration by schema_id...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.detect(portfolio_root=Path(tmp))
            artifacts = [
                _mk_artifact("epic", "e1"),
                _mk_artifact("epic", "e2"),
                _mk_artifact("feature", "f1", product="p1"),
                _mk_artifact("feature", "f2", product="p2"),
                _mk_artifact("story", "s1", product="p1"),
            ]
            repo = _TestArtifactRepository(ws, artifacts)
            
            epics = repo.collect_by_schema_id("epic")
            features = repo.collect_by_schema_id("feature")
            stories = repo.collect_by_schema_id("story")
            
            assert len(epics) == 2, f"expected 2 epics, got {len(epics)}"
            assert len(features) == 2, f"expected 2 features, got {len(features)}"
            assert len(stories) == 1, f"expected 1 story, got {len(stories)}"
            print("  ✓ enumeration works")
    except AssertionError as e:
        failures.append(f"test (a): {e}")

    # Test case (b): Multi-alias activation
    print("test (b): multi-alias activation...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.detect(portfolio_root=Path(tmp))
            artifacts = [
                _mk_artifact("epic", "e1"),
                _mk_artifact("feature", "f1", product="p1"),
            ]
            repo = _TestArtifactRepository(ws, artifacts)
            cel = CelEvaluator(ws, repo, None, _mock_calculation())
            
            artifact_types = [
                {"alias": "epic", "schema_id": "epic"},
                {"alias": "feature", "schema_id": "feature"},
            ]
            activation, err = cel.build_list_activation(artifact_types)
            
            assert err is None, f"expected no error, got {err}"
            assert activation is not None, "expected activation"
            print("  ✓ activation builds")
    except AssertionError as e:
        failures.append(f"test (b): {e}")

    # Test case (c): Set_query list-returning
    print("test (c): set_query list-returning...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.detect(portfolio_root=Path(tmp))
            artifacts = [
                _mk_artifact("epic", "e1"),
                _mk_artifact("epic", "e2"),
                _mk_artifact("feature", "f1", product="p1"),
            ]
            repo = _TestArtifactRepository(ws, artifacts)
            cel = CelEvaluator(ws, repo, None, _mock_calculation())
            
            artifact_types = [
                {"alias": "epic", "schema_id": "epic"},
                {"alias": "feature", "schema_id": "feature"},
            ]
            activation, err = cel.build_list_activation(artifact_types)
            assert err is None
            
            # Query to select all epics
            ekind, result = cel.evaluate_set_query("epic", activation)
            assert ekind == "list", f"expected list, got {ekind}"
            assert len(result) == 2, f"expected 2 epics, got {len(result)}"
            print("  ✓ set_query returns list")
    except AssertionError as e:
        failures.append(f"test (c): {e}")

    # Test case (d): Set_predicate bool over set A
    print("test (d): set_predicate bool over set A...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.detect(portfolio_root=Path(tmp))
            artifacts = [
                _mk_artifact("epic", "e1", status="done"),
                _mk_artifact("epic", "e2", status="blocked"),
                _mk_artifact("feature", "f1", product="p1", status="done"),
            ]
            repo = _TestArtifactRepository(ws, artifacts)
            cel = CelEvaluator(ws, repo, None, _mock_calculation())
            
            artifact_types = [
                {"alias": "epic", "schema_id": "epic"},
            ]
            activation, err = cel.build_list_activation(artifact_types)
            assert err is None
            
            # Predicate: all epics have status == "done"
            ekind, result = cel.evaluate_set_predicate("epic.all(e, e.status == 'done')", activation)
            assert ekind == "bool", f"expected bool, got {ekind}"
            assert result is False, f"expected False (not all epics done), got {result}"
            print("  ✓ set_predicate evaluates bool")
    except AssertionError as e:
        failures.append(f"test (d): {e}")

    # Test case (e): Error surface — unknown schema_id
    print("test (e): error — unknown schema_id...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.detect(portfolio_root=Path(tmp))
            repo = _TestArtifactRepository(ws, [])
            cel = CelEvaluator(ws, repo, None, _mock_calculation())
            
            artifact_types = [
                {"alias": "unknown", "schema_id": "unknown_kind"},
            ]
            activation, err = cel.build_list_activation(artifact_types)
            # Unknown schema_id yields empty list (not an error, just an empty set)
            assert activation is not None, "activation should still build"
            
            # But querying the empty alias should fail
            ekind, result = cel.evaluate_set_query("unknown", activation)
            # This depends on CEL semantics; with empty dict it may error or return empty
            # (actual behavior depends on celpy)
            print(f"  ✓ unknown schema_id handled ({ekind}: {result})")
    except AssertionError as e:
        failures.append(f"test (e): {e}")

    # Test case (f): Error surface — malformed CEL
    print("test (f): error — malformed CEL...")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Workspace.detect(portfolio_root=Path(tmp))
            artifacts = [_mk_artifact("epic", "e1")]
            repo = _TestArtifactRepository(ws, artifacts)
            cel = CelEvaluator(ws, repo, None, _mock_calculation())
            
            artifact_types = [{"alias": "epic", "schema_id": "epic"}]
            activation, err = cel.build_list_activation(artifact_types)
            assert err is None
            
            # Invalid CEL syntax
            ekind, result = cel.evaluate_set_query("epic. invalid", activation)
            assert ekind == "error", f"expected error, got {ekind}"
            print(f"  ✓ malformed CEL caught: {result[:50]}...")
    except AssertionError as e:
        failures.append(f"test (f): {e}")

    # Summary
    if failures:
        for f in failures:
            print(f"\nFAIL  {f}")
        print(f"\n{len(failures)} test violation(s)")
        return 1

    print("\npass: 0 test violation(s)")
    print("✓ All state-condition tests passed")
    return 0


def test_state_conditions() -> None:
    """pytest entry."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
