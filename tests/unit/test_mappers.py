"""Unit tests — the workspace access layer: Workspace, LogMapper (one test class per src class).
ArtifactMapper's discover/load pipeline is exercised end-to-end in
tests/integration/test_artifact_validation.py against the real schema catalog."""

from __future__ import annotations

import json

from mappers import LogMapper, Workspace


class TestWorkspace:
    def test_detect_defaults_workspace_under_framework_root(self, tmp_path):
        ws = Workspace.detect(tmp_path)
        assert ws.framework_root == tmp_path.resolve()
        # NOTE: the tests/conftest.py isolation fixture redirects the default workspace to tmp.
        assert ws.workspace_root.name == "workspace"

    def test_workspace_base_is_the_workspace_container(self, tmp_path):
        ws = Workspace(tmp_path, tmp_path / "data" / "workspace")
        assert ws.workspace_base == tmp_path / "data"

    def test_session_ledger_sanitizes_and_falls_back(self, tmp_path):
        ws = Workspace(tmp_path, tmp_path / "workspace")
        assert ws.session_ledger("a/b").name == "a-b.jsonl"
        assert ws.session_ledger(None).name == "session.jsonl"
        assert ws.session_ledger("x").parent == tmp_path / "workspace" / "logs" / "hooks"

    def test_run_journal_sanitizes_and_falls_back(self, tmp_path):
        ws = Workspace(tmp_path, tmp_path / "workspace")
        assert ws.run_journal("r/1").name == "r-1.jsonl"
        assert ws.run_journal(None).name == "run.jsonl"
        assert ws.run_journal("x").parent == tmp_path / "workspace" / "logs"

    def test_run_journals_exclude_hook_ledgers(self, tmp_path):
        ws = Workspace(tmp_path, tmp_path / "workspace")
        logs = tmp_path / "workspace" / "logs"
        (logs / "hooks").mkdir(parents=True)
        (logs / "r1.jsonl").write_text("{}\n")
        (logs / "hooks" / "s1.jsonl").write_text("{}\n")
        assert [p.name for p in ws.run_journals()] == ["r1.jsonl"]

    def test_label_relativizes_when_possible(self, tmp_path):
        ws = Workspace(tmp_path)
        inside = tmp_path / "a" / "b.md"
        assert ws.label(inside, tmp_path) == "a/b.md"
        outside = tmp_path.parent / "elsewhere.md"
        assert ws.label(outside, tmp_path) == str(outside)

    def test_resolve_trace_path_strips_workspace_prefix(self, tmp_path):
        ws = Workspace(tmp_path, tmp_path / "workspace")
        resolved = ws.resolve_trace_path("workspace/logs/{run}.jsonl", run="r1")
        assert resolved == tmp_path / "workspace" / "logs" / "r1.jsonl"


class TestLogMapper:
    def test_read_missing_path_returns_none(self, tmp_path):
        mapper = LogMapper(Workspace(tmp_path))
        assert mapper.read(None) is None
        assert mapper.read(tmp_path / "ghost.jsonl") is None

    def test_append_then_read_round_trip(self, tmp_path):
        mapper = LogMapper(Workspace(tmp_path))
        path = tmp_path / "logs" / "run.jsonl"
        mapper.append(path, {"step": "draft", "status": "completed"})
        mapper.append(path, {"step": "review", "status": "completed"})
        log = mapper.read(path)
        assert log.executed_steps() == ["draft", "review"]

    def test_read_skips_blank_and_malformed_lines(self, tmp_path):
        path = tmp_path / "run.jsonl"
        path.write_text('{"step": "ok", "status": "completed"}\n\nnot-json\n[1,2]\n')
        log = LogMapper(Workspace(tmp_path)).read(path)
        assert len(log.lines) == 1

    def test_append_entry_writes_envelope_omitting_null_fields(self, tmp_path):
        mapper = LogMapper(Workspace(tmp_path))
        path = tmp_path / "run.jsonl"
        mapper.append_entry(path, command="orchestrate", payload={"action": "dispatch"},
                            run="r1", step="draft", actor=None, status="dispatch")
        line = json.loads(path.read_text().strip())
        assert line["command"] == "orchestrate"
        assert line["run"] == "r1"
        assert line["payload"] == {"action": "dispatch"}
        assert "actor" not in line
