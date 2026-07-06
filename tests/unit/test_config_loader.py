"""Unit tests — the config loading plane: ConfigLoader (parse + contract-validate as one act)
and ConfigError (the aggregated findings carrier)."""

from __future__ import annotations

import pytest

from config import ConfigError, ConfigLoader
from models import Report

from support import CONTRACTS_DIR, write_conf

MINIMAL_ACL = """\
actors:
  - id: developer.agent.md
    roles: [developer]
roles:
  - id: developer
    privileges:
      - artifact: story.artifact.schema.json
        action: CREATE
"""


class TestConfigLoader:
    def test_load_returns_validated_mapping(self, loader, conf_root):
        write_conf(conf_root, "access-control-list", MINIMAL_ACL)
        data = loader.load("access-control-list")
        assert data["actors"][0]["id"] == "developer.agent.md"

    def test_missing_file_raises_config_error(self, loader):
        with pytest.raises(ConfigError, match="missing framework configuration file"):
            loader.load("access-control-list")

    def test_invalid_yaml_raises_config_error(self, loader, conf_root):
        write_conf(conf_root, "access-control-list", "actors: [unclosed")
        with pytest.raises(ConfigError, match="invalid YAML"):
            loader.load("access-control-list")

    def test_non_mapping_root_raises_config_error(self, loader, conf_root):
        write_conf(conf_root, "access-control-list", "- just\n- a\n- list\n")
        with pytest.raises(ConfigError, match="must be a YAML mapping"):
            loader.load("access-control-list")

    def test_contract_violation_raises_with_findings(self, loader, conf_root):
        # action verb outside the CREATE|READ|UPDATE|DELETE enum -> contract violation
        write_conf(conf_root, "access-control-list", MINIMAL_ACL.replace("CREATE", "EXECUTE"))
        with pytest.raises(ConfigError) as excinfo:
            loader.load("access-control-list")
        assert excinfo.value.report.has_errors()
        assert any("contract violation" in f.message for f in excinfo.value.report.findings)

    def test_every_violation_is_reported_not_just_the_first(self, loader, conf_root):
        broken = """\
actors:
  - id: not-an-agent-file
    roles: []
roles:
  - id: developer
    privileges:
      - artifact: story
        action: EXECUTE
"""
        write_conf(conf_root, "access-control-list", broken)
        with pytest.raises(ConfigError) as excinfo:
            loader.load("access-control-list")
        assert len(excinfo.value.report.findings) >= 2

    def test_missing_contract_schema_raises(self, conf_root, tmp_path):
        loader = ConfigLoader(conf_root, tmp_path / "no-contracts")
        write_conf(conf_root, "access-control-list", MINIMAL_ACL)
        with pytest.raises(ConfigError, match="missing contract schema"):
            loader.load("access-control-list")

    def test_paths_follow_naming_convention(self, loader, conf_root):
        assert loader.conf_path("workspace") == conf_root / "conf" / "workspace.conf.yaml"
        assert loader.contract_path("workspace") == CONTRACTS_DIR / "workspace.conf.schema.json"


class TestConfigError:
    def test_message_carries_first_finding(self):
        report = Report()
        report.error("a.yaml", "first problem")
        assert "first problem" in str(ConfigError(report))

    def test_message_counts_additional_findings(self):
        report = Report()
        report.error("a.yaml", "first problem")
        report.error("a.yaml", "second problem")
        report.error("a.yaml", "third problem")
        assert "+2 more findings" in str(ConfigError(report))

    def test_report_is_preserved_for_rendering(self):
        report = Report()
        report.error("a.yaml", "boom")
        assert ConfigError(report).report is report
