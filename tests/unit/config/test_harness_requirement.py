"""Unit tests for `HarnessRequirement` and its loader (core spec, Harness compatibility)."""

from __future__ import annotations

from pathlib import Path

import pytest

from config.harness_requirement import HarnessRequirement
from errors import ConfigurationError

from tests.unit.config.conftest import write_yaml


class TestHarnessRequirement:
    @pytest.mark.parametrize(
        ("require", "version", "admitted"),
        [
            (">=1.0.0a1,<1.1", "1.0.0-alpha.2", True),
            (">=1.0.0a1,<1.1", "1.0.0", True),
            (">=1.0.0a1,<1.1", "1.1.0", False),
            (">=1.0", "1.0.0-alpha.2", False),
            ("==1.0.0-alpha.2", "1.0.0a2", True),
        ],
    )
    def test_admits_reads_the_version_as_pep_440(
        self, require: str, version: str, admitted: bool
    ) -> None:
        assert HarnessRequirement(require).admits(version) is admitted

    def test_an_unparsable_installed_version_is_not_admitted(self) -> None:
        assert HarnessRequirement(">=0").admits("not-a-version") is False

    def test_a_require_that_is_not_a_specifier_set_is_refused(self) -> None:
        with pytest.raises(ValueError, match="PEP 440"):
            HarnessRequirement("one point oh")


class TestLoadHarnessRequirement:
    def test_loads_the_declaration(self, config_loader, framework_root: Path) -> None:
        write_yaml(framework_root / "conf" / "harness.conf.yaml", 'require: ">=1.0.0a1,<1.1"\n')

        loaded = config_loader.load_harness_requirement(framework_root)

        assert loaded == HarnessRequirement(">=1.0.0a1,<1.1")

    def test_a_missing_declaration_is_a_configuration_error(
        self, config_loader, framework_root: Path
    ) -> None:
        (framework_root / "conf" / "harness.conf.yaml").unlink()

        with pytest.raises(ConfigurationError) as raised:
            config_loader.load_harness_requirement(framework_root)
        assert raised.value.code == "missing-configuration-source"

    @pytest.mark.parametrize(
        "content", ["{}\n", "require: 1\n", 'require: ">=1"\nextra: no\n'], ids=["empty", "type", "extra"]
    )
    def test_an_off_contract_declaration_is_a_configuration_error(
        self, config_loader, framework_root: Path, content: str
    ) -> None:
        write_yaml(framework_root / "conf" / "harness.conf.yaml", content)

        with pytest.raises(ConfigurationError) as raised:
            config_loader.load_harness_requirement(framework_root)
        assert raised.value.code == "invalid-configuration-source"

    def test_a_require_that_is_not_pep_440_is_a_configuration_error(
        self, config_loader, framework_root: Path
    ) -> None:
        write_yaml(framework_root / "conf" / "harness.conf.yaml", 'require: "=>1.0"\n')

        with pytest.raises(ConfigurationError, match="PEP 440"):
            config_loader.load_harness_requirement(framework_root)
