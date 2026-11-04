"""Contract-validate a host adapter's own binding data at load — fail-fast, shared.

Every `adapters/<env>/` ships its own `tools.yaml` / `models.yaml`. Both are
contract-governed (`contracts/conf/adapters/*.conf.schema.json`) and are validated when
the adapter loads them, so a malformed binding stops the hook instead of silently
yielding empty probes — an empty probe list would classify every firing as a
pass-through, i.e. bypass every gate.

This module depends on PyYAML and jsonschema only — never on the harness core
(`config`, `services`, `stores`; adapter spec I15) — so any adapter can import it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator

ADAPTER_CONTRACTS_DIR = (
    Path(__file__).resolve().parent.parent / "contracts" / "conf" / "adapters"
)
TOOLS_CONTRACT = "tools.conf.schema.json"
MODELS_CONTRACT = "models.conf.schema.json"


class BindingContractError(ValueError):
    """Raised when an adapter binding file is missing, unparsable, or off-contract."""


def load_contract_bound_yaml(
    path: Path, contract: str, contracts_dir: Path = ADAPTER_CONTRACTS_DIR
) -> Mapping[str, Any]:
    """Load one binding YAML file and validate it against its adapter contract."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as failure:
        raise BindingContractError(
            f"adapter binding '{path}' cannot be loaded: {failure}"
        ) from failure
    validate_binding(document, contract, source=str(path), contracts_dir=contracts_dir)
    return document


def validate_binding(
    document: Any,
    contract: str,
    *,
    source: str,
    contracts_dir: Path = ADAPTER_CONTRACTS_DIR,
) -> None:
    """Validate one parsed binding document against its adapter contract."""
    schema_path = contracts_dir / contract
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        raise BindingContractError(
            f"adapter binding contract '{schema_path}' cannot be loaded: {failure}"
        ) from failure
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "<root>"
        raise BindingContractError(
            f"adapter binding '{source}' violates {contract} at '{location}': "
            f"{first.message}"
        )


def require_non_empty(document: Mapping[str, Any], key: str, *, source: str, why: str) -> None:
    """Refuse an empty probe list a gate depends on — it would fail open, not closed."""
    if not document.get(key):
        raise BindingContractError(f"adapter binding '{source}' declares no '{key}': {why}")


__all__ = [
    "ADAPTER_CONTRACTS_DIR",
    "BindingContractError",
    "MODELS_CONTRACT",
    "TOOLS_CONTRACT",
    "load_contract_bound_yaml",
    "require_non_empty",
    "validate_binding",
]
