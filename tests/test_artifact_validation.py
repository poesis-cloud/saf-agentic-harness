"""Portfolio Validity Invariant TESTS — valid-by-construction reads + write-boundary enforcement.

Covers:
  - ArtifactValidator flags a schema-invalid artifact;
  - ArtifactRepository.discover() RAISES InvalidArtifactError on an invalid universe, while
    scan_raw() tolerates it (so check-artifact can report), and an empty portfolio is fine;
  - load_one() infers an artifact's kind from its portfolio path;
  - the postcondition hook REVERTS an invalid write (deletes the untracked new file) and DENIES
    with the schema findings.

Run: python3 harness/tests/test_artifact_validation.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persistence import ArtifactRepository, InvalidArtifactError, LogRepository, SchemaRepository, Workspace
from services import AuthorizationPolicy, HookService
from utils import ArtifactValidator

# An epic frontmatter that parses but violates the epic schema (missing most required fields).
INVALID_EPIC = "---\nid: bad-epic\nstatus: funnel\n---\n# Bad epic\n"


def _ws(tmp: str) -> Workspace:
    return Workspace.detect(portfolio_root=Path(tmp))


def _repo(ws: Workspace) -> ArtifactRepository:
    return ArtifactRepository(ws, ArtifactValidator(ws, SchemaRepository(ws)))


def main() -> int:
    failures: list[str] = []

    def check(name: str, cond: bool, detail: str = "") -> None:
        print(f"  ok  {name}" if cond else f"  FAIL {name} {detail}")
        if not cond:
            failures.append(name)

    # (a) validator + scan_raw + load_one + discover-raises, over an invalid portfolio
    print("(a) invalid portfolio")
    with tempfile.TemporaryDirectory() as tmp:
        ws = _ws(tmp)
        (Path(tmp) / "epics").mkdir(parents=True)
        bad = Path(tmp) / "epics" / "bad-epic.md"
        bad.write_text(INVALID_EPIC)
        repo = _repo(ws)

        check("scan_raw tolerates invalid", len(repo.scan_raw()) == 1)
        art = repo.load_one(bad)
        check("load_one infers epic kind", art is not None and art.kind == "epic")
        report = ArtifactValidator(ws, SchemaRepository(ws)).validate(art)
        check("validator flags invalid artifact", report.has_errors(), str([f.message for f in report.findings][:1]))

        raised = False
        try:
            repo.discover()
        except InvalidArtifactError:
            raised = True
        check("discover raises InvalidArtifactError", raised)

    # (b) empty portfolio: discover returns [] with no raise
    print("(b) empty portfolio")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo(_ws(tmp))
        check("discover of empty portfolio is []", repo.discover() == [])

    # (c) postcondition hook reverts an invalid (untracked) write + denies
    print("(c) write-boundary enforcement")
    with tempfile.TemporaryDirectory() as tmp:
        ws = _ws(tmp)
        (Path(tmp) / "epics").mkdir(parents=True)
        bad = Path(tmp) / "epics" / "bad-epic.md"
        bad.write_text(INVALID_EPIC)
        repo = _repo(ws)
        hooks = HookService(ws, SchemaRepository(ws), LogRepository(ws), AuthorizationPolicy(), artifacts=repo)

        payload = {"tool": "create_file", "tool_input": {"filePath": str(bad)}}
        decision = hooks._postcondition(payload)
        check("hook denies invalid write", decision.permission == "deny", decision.reason[:70])
        check("hook reverts (deletes) untracked invalid write", not bad.exists())
        check("deny carries schema findings", decision.report.has_errors())

    if failures:
        print(f"\n{len(failures)} violation(s)")
        return 1
    print("\npass: 0 violation(s)")
    return 0


def test_artifact_validation() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
