"""Fixture builders for the write-boundary services — functions 8 and 9.

Kept beside `checking_fixtures` rather than inside it so the condition lane and the
write-boundary lane never share a mutable fixture surface.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from config import AccessControlList, Privilege, WorkspaceLayout
from config.artifact_node import ArtifactNode
from config.folder_node import FolderNode

ACTOR = "qa-engineer"
ARTIFACT_KIND = "review-report"
ARTIFACT_REF = "review-report/refunds.json"
SIBLING_REF = "review-report/chargebacks.json"
LOGS_REF = "logs/01j9xqr7t3.log.jsonl"
OUTSIDE_REF = "scratch/notes.json"

# The spec's worked example for function 8 (`check-step-authorization`).
EXAMPLE_REF = "portfolio/epics/epic-payments.md"
EXAMPLE_RESOURCE = "epic"


def build_workspace_layout() -> WorkspaceLayout:
    """Build the layout binding `review-report/<slug>.json` to the fixture artifact."""
    return WorkspaceLayout(
        nodes=(
            FolderNode(
                slug=ARTIFACT_KIND,
                description="Review report folder",
                children=(
                    ArtifactNode(
                        slug="<report-slug>.json",
                        description="One review report",
                        cardinality="0..*",
                        artifact=ARTIFACT_KIND,
                        template="review-report",
                    ),
                ),
            ),
        )
    )


def build_example_layout() -> WorkspaceLayout:
    """Build the layout the spec's function 8 example writes against."""
    return WorkspaceLayout(
        nodes=(
            FolderNode(
                slug="portfolio",
                description="Portfolio folder",
                children=(
                    FolderNode(
                        slug="epics",
                        description="Epic folder",
                        children=(
                            ArtifactNode(
                                slug="<epic-slug>.md",
                                description="One epic",
                                cardinality="0..*",
                                artifact=EXAMPLE_RESOURCE,
                                template="epic",
                            ),
                        ),
                    ),
                ),
            ),
        )
    )


def build_ambiguous_layout() -> WorkspaceLayout:
    """Build a layout where two artifact kinds' path patterns match one path."""
    return WorkspaceLayout(
        nodes=(
            FolderNode(
                slug=ARTIFACT_KIND,
                description="Review report folder",
                children=(
                    ArtifactNode(
                        slug="<report-slug>.json",
                        description="One review report",
                        cardinality="0..*",
                        artifact=ARTIFACT_KIND,
                        template="review-report",
                    ),
                    ArtifactNode(
                        slug="<audit-slug>.json",
                        description="One audit report",
                        cardinality="0..*",
                        artifact="audit-report",
                        template="audit-report",
                    ),
                ),
            ),
        )
    )


def build_access_control_list(
    *,
    actor: str = ACTOR,
    privileges: Sequence[tuple[str, str]] = (
        (ARTIFACT_KIND, "create"),
        (ARTIFACT_KIND, "update"),
        (ARTIFACT_KIND, "delete"),
    ),
) -> AccessControlList:
    """Build the ACL granting one actor a chosen privilege set."""
    return AccessControlList(
        grants={
            actor: frozenset(
                Privilege(artifact=artifact, action=action)
                for artifact, action in privileges
            )
        }
    )


def build_report_document(slug: str = "refunds", status: str = "draft") -> Mapping[str, Any]:
    """Build one fixture artifact document, valid or invalid per its `status`."""
    return {"slug": slug, "status": status}


def stage_artifact(workspace: Path, ref: str, document: Mapping[str, Any]) -> Path:
    """Land one write in the working tree — the staging area — without committing."""
    path = workspace / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def read_document(workspace: Path, ref: str) -> Mapping[str, Any] | None:
    """Read one workspace path's current bytes, or `None` when it is absent."""
    path = workspace / ref
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = [
    "ACTOR",
    "ARTIFACT_KIND",
    "ARTIFACT_REF",
    "EXAMPLE_REF",
    "EXAMPLE_RESOURCE",
    "LOGS_REF",
    "OUTSIDE_REF",
    "SIBLING_REF",
    "build_access_control_list",
    "build_ambiguous_layout",
    "build_example_layout",
    "build_report_document",
    "build_workspace_layout",
    "read_document",
    "stage_artifact",
]
