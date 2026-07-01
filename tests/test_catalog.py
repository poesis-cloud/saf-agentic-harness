"""Artifact catalog TEST — part of the constitution gate (``make verify``).

Replaces the former ``artifact-schemas`` CLI command: every ``*.artifact-template.md`` has a paired
``*.artifact.schema.json``, each schema is valid Draft-07, and each schema's ``x-artifact.template``
metadata links to an existing template.

Run:  ``python3 harness/tests/test_catalog.py``   (from the framework root)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from persistence import SchemaRepository, Workspace
from services import SchemaChecker


def _checker() -> SchemaChecker:
    ws = Workspace.detect()
    return SchemaChecker(ws, SchemaRepository(ws))


def test_artifact_catalog() -> None:
    report = _checker().catalog()
    errors = [f for f in report.findings if f.severity == "error"]
    assert not errors, "\n".join(f"{f.path}: {f.message}" for f in errors)


def main() -> int:
    report = _checker().catalog()
    errors = [f for f in report.findings if f.severity == "error"]
    for finding in errors:
        print(f"FAIL  {finding.path}: {finding.message}")
    print(f"\n{'FAIL' if errors else 'pass'}: {len(errors)} catalog error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
