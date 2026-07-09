"""ConfigLoader — parse a conf/*.conf.yaml file and validate it against its contract schema.

Parsing and validation are one act: `load(name)` reads `conf/<name>.conf.yaml`, parses the YAML,
validates the document against `harness/contracts/<name>.conf.schema.json`, and returns the raw
mapping — or raises `ConfigError` carrying every finding. Nothing else in the harness parses a
conf file; the typed view classes receive an already-validated document.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only in minimal Python runtimes
    jsonschema = None

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from models import Report

from .errors import ConfigError

# The harness's OWN contract schemas are harness-owned and live with the harness code —
# resolved structurally from this package, never configured (config files describe the
# embedding framework and the workspace, not the harness itself).
HARNESS_CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"
# Host adapter bindings are harness-owned INTERNAL configuration — same structural home.
HARNESS_ADAPTERS_DIR = Path(__file__).resolve().parents[2] / "adapters"


class ConfigLoader:
    """Reads and contract-validates the framework configuration files."""

    def __init__(self, framework_root: Path, contracts_dir: Path | None = None) -> None:
        self.framework_root = framework_root
        self.contracts_dir = contracts_dir or HARNESS_CONTRACTS_DIR

    # --- paths ----------------------------------------------------------------
    def conf_path(self, name: str) -> Path:
        return self.framework_root / "conf" / f"{name}.conf.yaml"

    def contract_path(self, name: str) -> Path:
        return self.contracts_dir / "conf" / f"{name}.conf.schema.json"

    # --- the one parse+validate act --------------------------------------------
    def load(self, name: str) -> dict[str, Any]:
        """Parse conf/<name>.conf.yaml and validate it against <name>.conf.schema.json."""
        return self.load_path(self.conf_path(name), name)

    def load_path(self, path: Path, contract_name: str) -> dict[str, Any]:
        """Parse an explicit YAML file and validate it against the named contract schema.
        Used for the per-file members of a config family (conf/workflows/*.workflow.conf.yaml
        all validate against the single workflow contract)."""
        report = Report()
        label = str(path)
        if yaml is None:
            report.error(label, "PyYAML is required to parse framework configuration")
            raise ConfigError(report)
        if not path.is_file():
            report.error(label, f"missing framework configuration file: {path}")
            raise ConfigError(report)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            report.error(label, f"invalid YAML: {exc}")
            raise ConfigError(report)
        if not isinstance(data, dict):
            report.error(label, "configuration root must be a YAML mapping")
            raise ConfigError(report)
        self._validate(data, contract_name, label, report)
        if report.has_errors():
            raise ConfigError(report)
        return data

    def _validate(self, data: dict[str, Any], contract_name: str, label: str, report: Report) -> None:
        contract_path = self.contract_path(contract_name)
        if jsonschema is None:
            report.error(label, "jsonschema is required to validate framework configuration")
            return
        if not contract_path.is_file():
            report.error(label, f"missing contract schema: {contract_path}")
            return
        try:
            schema = json.loads(contract_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            report.error(str(contract_path), f"invalid contract schema JSON: {exc.msg} at line {exc.lineno}")
            return
        validator_cls = jsonschema.validators.validator_for(schema)
        validator = validator_cls(schema)
        for error in sorted(validator.iter_errors(data), key=lambda item: list(item.absolute_path)):
            data_path = ".".join(str(part) for part in error.absolute_path)
            prefix = f"{data_path}: " if data_path else ""
            report.error(label, f"{contract_name} contract violation: {prefix}{error.message}")


__all__ = ["ConfigLoader"]
