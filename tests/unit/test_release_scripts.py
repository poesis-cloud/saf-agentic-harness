"""The release scripts CD runs: the marketplace bump and the manifest validation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
SHA = "a" * 40


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"release_{name}", SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUMP = _load("bump_marketplace")
VALIDATE = _load("validate_plugin_manifest")


def test_a_missing_catalogue_is_created_with_the_pinned_entry(tmp_path: Path) -> None:
    path = tmp_path / ".github" / "plugin" / "marketplace.json"

    assert BUMP.main([str(path), "poesis-saf-preview", "1.0.0-alpha.2", "plugin-v1.0.0-alpha.2", SHA]) == 0

    catalogue = json.loads(path.read_text(encoding="utf-8"))
    assert catalogue["name"] == "poesis-saf-preview"
    assert catalogue["plugins"] == [{
        "name": "poesis-saf-agentic-harness",
        "description": BUMP.DESCRIPTION,
        "license": "Apache-2.0",
        "version": "1.0.0-alpha.2",
        "source": {"source": "github", "repo": "poesis-cloud/saf-agentic-harness",
                   "ref": "plugin-v1.0.0-alpha.2", "sha": SHA},
    }]


def test_an_existing_entry_is_bumped_and_everything_else_kept(tmp_path: Path) -> None:
    path = tmp_path / "marketplace.json"
    path.write_text(json.dumps({
        "name": "poesis-saf", "owner": {"name": "Poesis SAS"}, "metadata": {"x": 1},
        "plugins": [
            {"name": "other", "source": "./other"},
            {"name": "poesis-saf-agentic-harness", "keywords": ["k"], "version": "0.9.0",
             "source": {"source": "github", "repo": "x/y", "ref": "old", "sha": "b" * 40}},
        ],
    }), encoding="utf-8")

    BUMP.main([str(path), "ignored", "1.0.0", "plugin-v1.0.0", SHA])

    catalogue = json.loads(path.read_text(encoding="utf-8"))
    assert catalogue["name"] == "poesis-saf" and catalogue["metadata"] == {"x": 1}
    assert catalogue["plugins"][0] == {"name": "other", "source": "./other"}
    entry = catalogue["plugins"][1]
    assert entry["keywords"] == ["k"] and entry["version"] == "1.0.0"
    assert entry["source"]["ref"] == "plugin-v1.0.0" and entry["source"]["sha"] == SHA


def test_a_short_sha_is_refused(tmp_path: Path) -> None:
    assert BUMP.main([str(tmp_path / "m.json"), "m", "1", "r", "abc"]) == 2


@pytest.mark.parametrize(("manifest", "code"), [
    ({"$schema": "urn:test", "name": "ok"}, 0),
    ({"$schema": "urn:test", "name": "Not Ok"}, 1),
    ({"$schema": "urn:other", "name": "ok"}, 1),
])
def test_the_manifest_is_held_to_the_schema_and_its_identifier(
    tmp_path: Path, manifest: dict, code: int
) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:test",
        "type": "object", "properties": {"name": {"pattern": "^[a-z]+$"}},
    }), encoding="utf-8")
    path = tmp_path / "plugin.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    assert VALIDATE.main(str(path), str(schema)) == code
