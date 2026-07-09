"""Unit tests — the configuration catalogs + aggregate: WorkflowCatalog, FrameworkConfig
(one test class per src class). SchemaCatalog is covered against the real catalog in
tests/integration/test_catalog.py; its unit-level error branches are exercised here."""

from __future__ import annotations

import json

import pytest

from config import ConfigError, ConfigLoader, FrameworkConfig, SchemaCatalog, WorkflowCatalog
from mappers import Workspace
from models import Report
from support import CONTRACTS_DIR, capabilities_yaml, write_conf

FRAMEWORK_CONF = "paths:\n  skills: .\n  schemas: schemas\n"


def _workflow_yaml(wid: str = "sample") -> str:
    return f"""\
workflow:
  id: {wid}
  kind: ceremony
  facilitator: '@scrum-master'
  steps:
  - id: draft
    actor: '@developer'
    capabilities:
{capabilities_yaml(6)}
    conditions: []
"""


def _write_workflow(root, wid: str, text: str | None = None):
    path = root / "conf" / "workflows" / f"{wid}.workflow.conf.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text is not None else _workflow_yaml(wid), encoding="utf-8")
    return path


class TestWorkflowCatalog:
    def _catalog(self, root) -> WorkflowCatalog:
        return WorkflowCatalog(root, ConfigLoader(root, CONTRACTS_DIR))

    def test_paths_are_sorted_and_suffix_scoped(self, conf_root):
        _write_workflow(conf_root, "zeta")
        _write_workflow(conf_root, "alpha")
        (conf_root / "conf" / "workflows" / "notes.txt").write_text("ignored")
        names = [p.name for p in self._catalog(conf_root).paths()]
        assert names == ["alpha.workflow.conf.yaml", "zeta.workflow.conf.yaml"]

    def test_load_parses_and_validates_in_one_act(self, conf_root):
        path = _write_workflow(conf_root, "sample")
        workflow = self._catalog(conf_root).load(path)
        assert str(workflow.id) == "sample"
        assert workflow.steps[0].capabilities["deep-reasoning"] == 7.0

    def test_load_rejects_step_without_capabilities(self, conf_root):
        bad = """\
workflow:
  id: bad
  kind: ceremony
  facilitator: '@scrum-master'
  steps:
  - id: draft
    actor: '@developer'
    conditions: []
"""
        path = _write_workflow(conf_root, "bad", bad)
        with pytest.raises(ConfigError, match="capabilities"):
            self._catalog(conf_root).load(path)

    def test_find_resolves_by_workflow_id(self, conf_root):
        _write_workflow(conf_root, "alpha")
        _write_workflow(conf_root, "beta")
        catalog = self._catalog(conf_root)
        assert str(catalog.find("beta").id) == "beta"
        assert catalog.find("ghost") is None

    def test_all_is_cached(self, conf_root):
        _write_workflow(conf_root, "alpha")
        catalog = self._catalog(conf_root)
        assert catalog.all() is catalog.all()


class TestFrameworkConfig:
    def test_detect_loads_and_validates_the_real_configuration(self):
        config = FrameworkConfig.detect(Workspace.detect())
        assert config.model_profiles.models(), "the real model catalog must not be empty"
        assert config.access_control_list.agents(), "the real ACL must declare agents"
        assert config.workspace_layout.nodes(), "the real workspace layout must declare nodes"
        assert config.workflows.all(), "the real workflow catalog must not be empty"

    def test_validate_all_reports_no_findings_on_the_real_configuration(self):
        report = FrameworkConfig.detect(Workspace.detect()).validate_all()
        assert not report.has_errors(), [f.message for f in report.findings]

    def test_broken_single_file_config_fails_at_construction(self, conf_root):
        # A valid ACL -> FrameworkConfig must raise, not defer.
        # NOTE: conf/framework.conf.yaml is legacy plumbing FrameworkConfig still hard-requires
        # to construct; the target design sources framework layout from .env (harness/def/spec.md
        # Configuration plane). Written here only so construction can proceed to the ACL check —
        # not itself a tested concern, and slated for removal once the harness is reimplemented
        # against the .env-based spec.
        write_conf(conf_root, "framework", FRAMEWORK_CONF)
        write_conf(conf_root, "access-control-list", "actors: []\nroles: []\n")
        with pytest.raises(ConfigError):
            FrameworkConfig(Workspace(conf_root, conf_root / "workspace"))

    def test_broken_workflow_family_is_reported_by_validate_all(self, conf_root, tmp_path):
        # Valid single-file configs; one invalid workflow file -> construction succeeds
        # (workflows are lazy) and validate_all() surfaces the violation.
        real_conf = Workspace.detect().framework_root / "conf"
        # conf/framework.conf.yaml: same legacy-plumbing note as above — not under test here.
        write_conf(conf_root, "framework", FRAMEWORK_CONF)
        for name in ("access-control-list", "model-profiles", "workspace"):
            write_conf(conf_root, name, (real_conf / f"{name}.conf.yaml").read_text(encoding="utf-8"))
        _write_workflow(conf_root, "broken", "workflow:\n  id: broken\n")
        config = FrameworkConfig(Workspace(conf_root, conf_root / "workspace"))
        report = config.validate_all()
        assert report.has_errors()


class TestSchemaCatalog:
    def _workspace(self, tmp_path):
        # A colocated framework shape: harness/ marks the skills root; schemas/ is the registry.
        (tmp_path / "harness" / "contracts").mkdir(parents=True)
        (tmp_path / "schemas").mkdir()
        return Workspace(tmp_path, tmp_path / "workspace")

    def _write_schema(self, tmp_path, name: str, doc: dict):
        (tmp_path / "schemas" / f"{name}.artifact.schema.json").write_text(json.dumps(doc))

    def test_load_raw_indexes_by_x_artifact_id(self, tmp_path):
        ws = self._workspace(tmp_path)
        self._write_schema(tmp_path, "epic", {"type": "object", "x-artifact": {"id": "epic", "kind": "epic"}})
        schemas = SchemaCatalog(ws).load_raw(Report())
        assert set(schemas) == {"epic"}

    def test_load_raw_rejects_id_filename_mismatch(self, tmp_path):
        ws = self._workspace(tmp_path)
        self._write_schema(tmp_path, "epic", {"type": "object", "x-artifact": {"id": "not-epic"}})
        report = Report()
        assert SchemaCatalog(ws).load_raw(report) == {}
        assert any("must match filename stem" in f.message for f in report.findings)

    def test_load_raw_rejects_invalid_json(self, tmp_path):
        ws = self._workspace(tmp_path)
        (tmp_path / "schemas" / "bad.artifact.schema.json").write_text("{not json")
        report = Report()
        assert SchemaCatalog(ws).load_raw(report) == {}
        assert report.has_errors()

    def test_load_raw_rejects_missing_x_artifact_id(self, tmp_path):
        ws = self._workspace(tmp_path)
        self._write_schema(tmp_path, "epic", {"type": "object"})
        report = Report()
        assert SchemaCatalog(ws).load_raw(report) == {}
        assert any("x-artifact.id" in f.message for f in report.findings)

    def test_template_stems(self):
        from pathlib import Path
        assert SchemaCatalog.template_stem_from_schema(Path("epic.artifact.schema.json")) == "epic"
        assert SchemaCatalog.template_stem_from_template(Path("epic.artifact-template.md")) == "epic"


class TestAdapterBinding:
    def test_known_env_binding_is_loaded_and_validated(self):
        from mappers import Workspace
        config = FrameworkConfig.detect(Workspace.detect())
        binding = config.adapter_binding("github-copilot")
        assert "runSubagent" in binding["dispatch_tools"]
        assert binding["write_tools"]["create_file"] == "create"

    def test_unknown_env_raises_config_error(self):
        from mappers import Workspace
        config = FrameworkConfig.detect(Workspace.detect())
        with pytest.raises(ConfigError, match="missing framework configuration file"):
            config.adapter_binding("no-such-host")
