import json
from pathlib import Path

import pytest
import yaml
from jsonschema import validators
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


ADAPTER_ENV = "vscode-github-copilot-chat"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def adapter_dir(repo_root: Path) -> Path:
    return repo_root / "adapters" / ADAPTER_ENV


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def hooks_yaml(adapter_dir: Path) -> dict:
    return _load_yaml(adapter_dir / "hooks.yaml")


@pytest.fixture(scope="session")
def models_yaml(adapter_dir: Path) -> dict:
    return _load_yaml(adapter_dir / "models.yaml")


@pytest.fixture(scope="session")
def tools_yaml(adapter_dir: Path) -> dict:
    return _load_yaml(adapter_dir / "tools.yaml")


@pytest.fixture(scope="session")
def hook_stdin_schema(adapter_dir: Path) -> dict:
    return _load_json(adapter_dir / "contracts" / "hook-stdin.schema.json")


@pytest.fixture(scope="session")
def hook_stdout_schema(adapter_dir: Path) -> dict:
    return _load_json(adapter_dir / "contracts" / "hook-stdout.schema.json")


def _resource_from_schema(schema: dict) -> Resource:
    try:
        return Resource.from_contents(schema, default_specification=DRAFT202012)
    except TypeError:
        return Resource.from_contents(schema)


@pytest.fixture(scope="session")
def contract_registry(repo_root: Path) -> Registry:
    registry = Registry()
    schema_paths = [
        *repo_root.glob("contracts/**/*.schema.json"),
        *repo_root.glob("adapters/*/contracts/*.schema.json"),
    ]
    for schema_path in schema_paths:
        schema = _load_json(schema_path)
        schema_id = schema.get("$id")
        if schema_id:
            registry = registry.with_resource(schema_id, _resource_from_schema(schema))
    return registry


@pytest.fixture
def make_validator(contract_registry: Registry):
    def _make_validator(schema: dict):
        validator_cls = validators.validator_for(schema)
        validator_cls.check_schema(schema)
        return validator_cls(schema, registry=contract_registry)

    return _make_validator
