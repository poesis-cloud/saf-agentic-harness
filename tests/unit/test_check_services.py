"""Unit tests — the check services: StepChecker (the check-step router) and ArtifactChecker
(the state plane: scope coherence + parent linkage). Collaborators are in-memory doubles; the
CEL plane itself is covered by tests/integration/test_state_conditions.py."""

from __future__ import annotations

from pathlib import Path

from config import Workflow
from mappers import LogMapper, Workspace
from models import Artifact, Report
from services import ArtifactChecker, StepChecker

CAPS = {"deep-reasoning": 5}


class _StubWorkflows:
    def __init__(self, *workflows: Workflow) -> None:
        self._workflows = list(workflows)

    def find(self, orchestration_id: str):
        return next((w for w in self._workflows if str(w.id) == orchestration_id), None)


class _StubArtifacts:
    def __init__(self, universe: list[Artifact]) -> None:
        self._universe = universe

    def resolve_unit(self, unit_id: str):
        return next((a for a in self._universe if a.artifact_id == unit_id), None)

    def scan_raw(self):
        return list(self._universe)

    def select(self, unit_id: str, kinds):
        return [a for a in self._universe
                if a.artifact_id == unit_id and (kinds is None or a.kind in kinds)]


class _StubCel:
    """evaluate_state returns a scripted (outcome, detail) per condition id order."""

    def __init__(self, outcome: str = "pass", detail: str = "ok") -> None:
        self.outcome, self.detail = outcome, detail

    def evaluate_state(self, selector, predicate_src, unit_id=None):
        return self.outcome, self.detail


class _StubSchemaChecker:
    def conformance(self, artifacts):
        return Report()


def _artifact(kind: str, artifact_id: str, scope: str | None = None, **fields) -> Artifact:
    fields = {"id": artifact_id, **fields}
    return Artifact(kind, Path(f"/tmp/{artifact_id}.md"), fields, "", scope_slug=scope)


def _workflow(steps: list[dict], wid: str = "wf") -> Workflow:
    # StepChecker labels findings with workflow.path — the config catalog always provides one,
    # so synthetic workflows must too (an implicit contract of Workflow.path).
    return Workflow({"workflow": {"id": wid, "steps": steps}}, path=Path(f"/tmp/{wid}.workflow.conf.yaml"))


def _step_checker(tmp_path, workflow: Workflow, universe=None, cel=None) -> StepChecker:
    ws = Workspace(tmp_path, tmp_path / "workspace")
    return StepChecker(ws, _StubWorkflows(workflow), _StubArtifacts(universe or []),
                       LogMapper(ws), cel or _StubCel(), _StubSchemaChecker())


class TestStepChecker:
    STEPS = [
        {"id": "draft", "actor": "@dev", "capabilities": CAPS, "conditions": []},
        {"id": "review", "actor": "@qa", "capabilities": CAPS, "conditions": [
            {"id": "after-draft", "kind": "precondition", "type": "after", "step_id": "draft"},
        ]},
    ]

    def test_unknown_orchestration_is_an_error(self, tmp_path):
        checker = _step_checker(tmp_path, _workflow(self.STEPS))
        report = checker.check_step("ghost", "draft", "u-1")
        assert report.has_errors()

    def test_unknown_step_is_an_error(self, tmp_path):
        checker = _step_checker(tmp_path, _workflow(self.STEPS))
        report = checker.check_step("wf", "ghost", "u-1")
        assert any("not declared" in f.message for f in report.findings)

    def test_after_condition_passes_when_predecessor_logged_complete(self, tmp_path):
        checker = _step_checker(tmp_path, _workflow(self.STEPS))
        log_path = tmp_path / "run.jsonl"
        checker.logs.append(log_path, {"step": "draft", "status": "completed"})
        report = checker.check_step("wf", "review", "u-1", log_path)
        assert not report.has_errors()

    def test_after_condition_fails_when_predecessor_missing(self, tmp_path):
        checker = _step_checker(tmp_path, _workflow(self.STEPS))
        log_path = tmp_path / "run.jsonl"
        checker.logs.append(log_path, {"step": "other", "status": "completed"})
        report = checker.check_step("wf", "review", "u-1", log_path)
        assert report.has_errors()
        assert any(f.condition_id == "after-draft" for f in report.findings)

    def test_after_condition_skips_without_a_run_log(self, tmp_path):
        checker = _step_checker(tmp_path, _workflow(self.STEPS))
        report = checker.check_step("wf", "review", "u-1", log_path=None)
        assert not report.has_errors()  # skipped, not failed

    def test_state_condition_failure_is_a_finding(self, tmp_path):
        steps = [{"id": "s", "actor": "@dev", "capabilities": CAPS, "conditions": [
            {"id": "all-done", "kind": "postcondition", "type": "state",
             "set_selector": {"set_type": "artifact"}, "set_predicate": "selected.all(x, true)"},
        ]}]
        checker = _step_checker(tmp_path, _workflow(steps), cel=_StubCel("fail", "2 of 3 not done"))
        report = checker.check_step("wf", "s", "u-1")
        assert report.has_errors()

    def test_state_condition_error_reports_the_detail(self, tmp_path):
        steps = [{"id": "s", "actor": "@dev", "capabilities": CAPS, "conditions": [
            {"id": "broken", "kind": "postcondition", "type": "state",
             "set_selector": {"set_type": "artifact"}, "set_predicate": "junk("},
        ]}]
        checker = _step_checker(tmp_path, _workflow(steps), cel=_StubCel("error", "malformed CEL"))
        report = checker.check_step("wf", "s", "u-1")
        assert any("malformed CEL" in f.message for f in report.findings)

    def test_state_condition_requires_id_selector_and_predicate(self, tmp_path):
        steps = [{"id": "s", "actor": "@dev", "capabilities": CAPS, "conditions": [
            {"kind": "postcondition", "type": "state"},
        ]}]
        checker = _step_checker(tmp_path, _workflow(steps))
        report = checker.check_step("wf", "s", "u-1")
        assert any("missing its required id" in f.message for f in report.findings)

    def test_unknown_condition_type_is_an_error(self, tmp_path):
        steps = [{"id": "s", "actor": "@dev", "capabilities": CAPS, "conditions": [
            {"id": "c", "kind": "precondition", "type": "when"},
        ]}]
        checker = _step_checker(tmp_path, _workflow(steps))
        report = checker.check_step("wf", "s", "u-1")
        assert any("unknown condition type" in f.message for f in report.findings)

    def test_record_appends_the_canonical_step_line(self, tmp_path):
        checker = _step_checker(tmp_path, _workflow(self.STEPS))
        log_path = tmp_path / "run.jsonl"
        checker.check_step("wf", "draft", "u-1", log_path, record=True)
        log = checker.logs.read(log_path)
        assert log.executed_steps() == ["draft"]
        entry = log.entries()[0]
        assert entry.command == "check-step"
        assert entry.unit == "u-1"

    def test_review_session_re_evaluates_recorded_steps(self, tmp_path):
        checker = _step_checker(tmp_path, _workflow(self.STEPS))
        ledger = tmp_path / "session.jsonl"
        checker.check_step("wf", "draft", "u-1", ledger, record=True)
        report = checker.review_session(ledger)
        assert not report.has_errors()
        # the re-evaluation is read-only; exactly ONE durable session-review summary is appended
        entries = checker.logs.read(ledger).entries()
        assert len(entries) == 2
        summary = entries[-1]
        assert summary.command == "session-review"
        assert summary.status == "completed"
        assert summary.payload["steps"] == 1
        assert summary.payload["conditions"]["fail"] == 0


class TestArtifactChecker:
    def _checker(self, tmp_path, universe) -> ArtifactChecker:
        ws = Workspace(tmp_path, tmp_path / "workspace")
        (tmp_path / "workspace").mkdir(exist_ok=True)
        return ArtifactChecker(ws, _StubArtifacts(universe), _StubSchemaChecker())

    def test_parent_linkage_resolves_across_the_universe(self, tmp_path):
        epic = _artifact("epic", "E-1")
        feature = _artifact("feature", "F-1", parent_epic="E-1")
        checker = self._checker(tmp_path, [epic, feature])
        assert not checker.check_artifact_rules([feature]).has_errors()

    def test_dangling_parent_reference_is_an_error(self, tmp_path):
        feature = _artifact("feature", "F-1", parent_epic="E-404")
        checker = self._checker(tmp_path, [feature])
        report = checker.check_artifact_rules([feature])
        assert any("does not resolve" in f.message for f in report.findings)

    def test_scope_frontmatter_mismatch_is_warned(self, tmp_path):
        scoped = _artifact("story", "S-1", scope="product-a", product="product-b")
        checker = self._checker(tmp_path, [scoped])
        report = checker.check_artifact_rules([scoped])
        assert any("does not match path scope" in f.message for f in report.findings)
        assert not report.has_errors()  # a warning, not an error

    def test_check_target_requires_a_globally_unique_unit(self, tmp_path):
        twin_a = _artifact("story", "S-1", scope="product-a")
        twin_b = _artifact("story", "S-1", scope="product-b")
        checker = self._checker(tmp_path, [twin_a, twin_b])
        report, targets = checker.check_target("S-1", None)
        assert any("not globally unique" in f.message for f in report.findings)

    def test_check_target_unknown_unit_is_an_error(self, tmp_path):
        checker = self._checker(tmp_path, [])
        report, targets = checker.check_target("ghost", None)
        assert report.has_errors()
        assert targets == []

    def test_check_all_requires_a_workspace(self, tmp_path):
        ws = Workspace(tmp_path, tmp_path / "nowhere")
        checker = ArtifactChecker(ws, _StubArtifacts([]), _StubSchemaChecker())
        assert checker.check_all().has_errors()

    def test_check_all_warns_on_empty_workspace(self, tmp_path):
        checker = self._checker(tmp_path, [])
        report = checker.check_all()
        assert any("no artifacts found" in f.message for f in report.findings)
