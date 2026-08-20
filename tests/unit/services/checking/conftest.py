"""Pytest fixtures for the checking services: tmp-dir Git workspaces and real logs.

Isolation comes from constructor injection and tmp-dir workspaces only — no
collaborator is ever monkey-patched (spec, "Unit testing").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from checking_fixtures import run_git
from config import AccessControlList, Privilege, WorkspaceLayout
from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode
from stores.artifact_store import ArtifactStore
from stores.session_log_store import SessionLogStore


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """Create a tmp-dir Git workspace with a baseline commit."""
    run_git(tmp_path, "init")
    run_git(tmp_path, "config", "user.email", "test@example.com")
    run_git(tmp_path, "config", "user.name", "Test User")
    run_git(tmp_path, "commit", "--allow-empty", "-m", "baseline")
    return tmp_path


@pytest.fixture()
def artifact_schema(tmp_path: Path) -> Path:
    """Write the fixture artifact schema every fixture artifact is bound to."""
    schema_path = tmp_path / "review-report.artifact.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "gsmarc://tests/contracts/review-report-artifact/v1",
                "$ref": "gsmarc://saf/contracts/artifact/v1",
                "properties": {"status": {"enum": ["draft", "approved"]}},
            }
        ),
        encoding="utf-8",
    )
    return schema_path


@pytest.fixture()
def artifact_store(workspace: Path, artifact_schema: Path) -> ArtifactStore:
    """Build the real store over the tmp workspace — the injected Git plane."""
    return ArtifactStore(workspace, {"review-report": artifact_schema})


@pytest.fixture()
def log_store(workspace: Path) -> SessionLogStore:
    """Build the real session log store over the tmp workspace."""
    return SessionLogStore(workspace)


@pytest.fixture()
def access_control_list() -> AccessControlList:
    """Build the ACL granting the fixture actor create/update on `review-report`."""
    return AccessControlList(
        grants={
            "qa-engineer": frozenset(
                {
                    Privilege(artifact="review-report", action="create"),
                    Privilege(artifact="review-report", action="update"),
                }
            ),
            "product-owner": frozenset(),
        }
    )


@pytest.fixture()
def workspace_layout() -> WorkspaceLayout:
    """Build the layout resolving `review-report/<name>.json` and the logs plane."""
    return WorkspaceLayout(
        nodes=(
            FolderNode(
                slug="review-report",
                description="Review reports.",
                children=(
                    ArtifactNode(
                        slug="<name>.json",
                        description="One review report.",
                        artifact="review-report",
                    ),
                ),
            ),
            FolderNode(slug="logs", description="Harness logs.", children=()),
        )
    )
