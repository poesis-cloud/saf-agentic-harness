"""Test isolation — never write runtime output into the framework repository.

In production ``Workspace.detect()`` defaults the workspace (the writable data root: run
journals, hook ledgers, and any staged artifact) to ``<framework-root>/workspace``. Under test
that would create a ``workspace/`` tree inside the repo. This autouse fixture redirects the
default to a per-test temporary directory, so the workspace never touches the repo. Tests that
pass an explicit ``workspace_root`` are left untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from mappers.workspace import Workspace


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    original = Workspace.detect.__func__

    def detect(cls, framework_root=None, workspace_root=None):
        if workspace_root is None:
            workspace_root = tmp_path / "workspace"
        return original(cls, framework_root, workspace_root)

    monkeypatch.setattr(Workspace, "detect", classmethod(detect))
