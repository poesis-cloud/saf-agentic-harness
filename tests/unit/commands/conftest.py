"""Shared fixtures for the command-layer tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils import SchemaValidator

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "contracts"


@pytest.fixture(scope="session")
def schema_validator() -> SchemaValidator:
    """Compile the repository contracts once for the whole command suite."""
    return SchemaValidator.compile_contracts(CONTRACTS_ROOT.rglob("*.schema.json"))
