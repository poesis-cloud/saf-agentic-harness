"""Test-support helpers for the unit suite (importable module, kept out of conftest)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts"

# A full capabilities block (every tag explicit) reusable by workflow-step fixtures.
FULL_CAPABILITIES = {
    "deep-reasoning": 7,
    "coding": 0,
    "tool-use": 4,
    "long-context": 6,
    "multimodal": 0,
    "writing-quality": 8,
    "instruction-following": 8,
    "fast-iteration": 2,
    "schema-adherence": 8,
}


def capabilities_yaml(indent: int) -> str:
    pad = " " * indent
    return "\n".join(f"{pad}{tag}: {weight}" for tag, weight in FULL_CAPABILITIES.items())


def write_conf(root: Path, name: str, text: str) -> Path:
    path = root / "conf" / f"{name}.conf.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
