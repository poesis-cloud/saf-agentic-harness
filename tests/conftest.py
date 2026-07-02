"""Test isolation — never write runtime output into the framework repository.

In production ``Workspace.detect()`` defaults the portfolio (the writable data root: run
journals, hook ledgers, and any staged artifact) to ``<framework-root>/portfolio``. Under test
that would create a ``portfolio/`` tree inside the repo. This autouse fixture redirects the
default to a per-test temporary directory, so the portfolio never touches the repo. Tests that
pass an explicit ``portfolio_root`` are left untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mappers.workspace import Workspace


@pytest.fixture(autouse=True)
def _isolated_portfolio(tmp_path, monkeypatch):
    original = Workspace.detect.__func__

    def detect(cls, framework_root=None, portfolio_root=None):
        if portfolio_root is None:
            portfolio_root = tmp_path / "portfolio"
        return original(cls, framework_root, portfolio_root)

    monkeypatch.setattr(Workspace, "detect", classmethod(detect))
