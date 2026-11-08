#!/usr/bin/env python3
"""Validate a rendered plugin's `plugin.json` against the official Agent Plugins schema.

    python .github/scripts/validate_plugin_manifest.py <plugin.json> <plugin.schema.json>
"""

from __future__ import annotations

import json
import sys

import jsonschema


def main(manifest_path: str, schema_path: str) -> int:
    with open(schema_path, encoding="utf-8") as handle:
        schema = json.load(handle)
    with open(manifest_path, encoding="utf-8") as handle:
        manifest = json.load(handle)
    validator = jsonschema.validators.validator_for(schema)(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda error: list(error.path))
    for error in errors:
        print(f"{manifest_path}: /{'/'.join(map(str, error.path))}: {error.message}")
    if manifest.get("$schema") != schema.get("$id"):
        print(f"{manifest_path}: $schema is not {schema.get('$id')}")
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
