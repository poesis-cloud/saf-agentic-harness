"""Shared fixtures for the unit suite.

Unit tests exercise ONE src class each (one test class per src class), with synthetic data.
Configuration-plane tests combine synthetic conf documents with the REAL contract schemas in
harness/contracts/ — the contracts are part of the behavior under test. End-to-end suites that
drive the real conf/ + workspace live in tests/integration/ instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

from config import ConfigLoader
from support import CONTRACTS_DIR


@pytest.fixture
def conf_root(tmp_path: Path) -> Path:
    """A synthetic framework root with an empty conf/ directory."""
    (tmp_path / "conf").mkdir()
    return tmp_path


@pytest.fixture
def loader(conf_root: Path) -> ConfigLoader:
    """A ConfigLoader over the synthetic conf/ + the real contract schemas."""
    return ConfigLoader(conf_root, CONTRACTS_DIR)
