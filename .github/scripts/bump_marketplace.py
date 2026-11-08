#!/usr/bin/env python3
"""Point a marketplace catalogue's harness plugin entry at a released plugin commit.

    python .github/scripts/bump_marketplace.py <marketplace.json> <marketplace name> \
        <version> <ref> <sha>

Creates the catalogue, or the plugin entry, when absent; otherwise bumps the entry's
`version` and its GitHub `source` (`ref`, `sha`) and leaves everything else as it was.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN = "poesis-saf-agentic-harness"
REPOSITORY = "poesis-cloud/saf-agentic-harness"
DESCRIPTION = (
    "Deterministic governance - regulation - supervision - coordination of agentic "
    "organization: workflow orchestration; step, model, skill, instruction resolution; "
    "artifact transaction and versioning; logging; agent authorization."
)


def bump(path: Path, marketplace: str, version: str, ref: str, sha: str) -> dict:
    if path.is_file():
        catalogue = json.loads(path.read_text(encoding="utf-8"))
    else:
        catalogue = {"name": marketplace, "owner": {"name": "Poesis SAS"}, "plugins": []}
    plugins = catalogue.setdefault("plugins", [])
    entry = next((plugin for plugin in plugins if plugin.get("name") == PLUGIN), None)
    if entry is None:
        entry = {"name": PLUGIN, "description": DESCRIPTION, "license": "Apache-2.0"}
        plugins.append(entry)
    entry["version"] = version
    entry["source"] = {"source": "github", "repo": REPOSITORY, "ref": ref, "sha": sha}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalogue, indent=2) + "\n", encoding="utf-8")
    return catalogue


def main(arguments: list[str]) -> int:
    if len(arguments) != 5 or len(arguments[4]) != 40:
        print(__doc__, file=sys.stderr)
        return 2
    bump(Path(arguments[0]), *arguments[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
