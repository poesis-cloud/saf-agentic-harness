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
# The framework's model catalogue, where the core's config loader reads it.
MODEL_CATALOGUE_PATH = Path("conf") / "model-profiles.conf.yaml"
_MODEL_BINDINGS_KEY = "modelBindings"
_MODEL_PROFILES_KEY = "modelProfiles"
_PROFILE_SLUG_KEY = "slug"


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


def require_catalogue_bound(
    models: Mapping[str, Any], framework_dir: Path, *, source: str
) -> None:
    """Refuse a framework model catalogue naming a profile this adapter does not bind.

    Core function 4: unroutable configurations die at load. A catalogue slug absent from
    `models.yaml` could be resolved by `resolve-step-model` yet never run on this host, so
    the hook stops here. The catalogue is re-read on every load, never cached at install:
    the host's model list (and so `models.yaml`) can evolve after the harness install.
    """
    catalogue_path = Path(framework_dir) / MODEL_CATALOGUE_PATH
    try:
        catalogue = yaml.safe_load(catalogue_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as failure:
        raise BindingContractError(
            f"framework model catalogue '{catalogue_path}' cannot be loaded, so the "
            f"adapter binding '{source}' cannot be checked against it: {failure}"
        ) from failure
    profiles = catalogue.get(_MODEL_PROFILES_KEY) if isinstance(catalogue, Mapping) else None
    if not isinstance(profiles, list) or not all(
        isinstance(profile, Mapping) and isinstance(profile.get(_PROFILE_SLUG_KEY), str)
        for profile in profiles
    ):
        raise BindingContractError(
            f"framework model catalogue '{catalogue_path}' is not a "
            f"'{_MODEL_PROFILES_KEY}' list of profiles with a '{_PROFILE_SLUG_KEY}'"
        )
    bound = models.get(_MODEL_BINDINGS_KEY) or {}
    unbound = sorted(
        {profile[_PROFILE_SLUG_KEY] for profile in profiles} - set(bound)
    )
    if unbound:
        raise BindingContractError(
            f"framework model catalogue '{catalogue_path}' names model profile(s) the "
            f"adapter binding '{source}' does not bind: {', '.join(unbound)} — every "
            "catalogue slug must map to a model this host offers; update models.yaml to "
            "the host's available models, or drop those profiles from the catalogue"
        )
