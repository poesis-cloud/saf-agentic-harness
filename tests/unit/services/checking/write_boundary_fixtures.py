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
from stores.session_log_store import Report
from utils.schema_validator import SchemaValidator

_CONTRACTS_DIR = Path(__file__).resolve().parents[4] / "contracts"
_contract_validator: SchemaValidator | None = None

ACTOR = "qa-engineer"
ARTIFACT_KIND = "review-report"
ARTIFACT_REF = "review-report/refunds.json"
SIBLING_REF = "review-report/chargebacks.json"
LOGS_REF = "logs/01j9xqr7t3.log.jsonl"

# A logs path no session has opened: absent from the working tree, so the staging
# baseline is clean and only invariant 6 can refuse a write to it.
ABSENT_LOGS_REF = "logs/01jabsent.log.jsonl"
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


def build_logs_binding_layout() -> WorkspaceLayout:
    """Build a layout that BINDS the logs path to an ordinary artifact kind.

    Invariant 6 is unconditional, so proving it needs a layout where every other
    deny cause is out of the way: the logs path resolves to a real resource the
    actor can be granted.
    """
    return WorkspaceLayout(
        nodes=(
            FolderNode(
                slug="logs",
                description="Harness logs",
                children=(
                    ArtifactNode(
                        slug="<session-id>.log.jsonl",
                        description="One session log",
                        cardinality="0..*",
                        artifact=ARTIFACT_KIND,
                        template="review-report",
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


def list_contract_violations(report: Report) -> tuple[str, ...]:
    """List a report's violations of its OWN output contract, rendered as prose.

    The report identity rule makes the rendered report the function's whole
    observable surface, so every branch is asserted against the contract that
    binds it — `unevaluatedProperties: false` is what forbids a payload on the
    error branches.
    """
    global _contract_validator
    if _contract_validator is None:
        _contract_validator = SchemaValidator.compile_contracts(
            sorted(_CONTRACTS_DIR.rglob("*.schema.json"))
        )
    records = _contract_validator.validate_instance(
        type(report).CONTRACT_ID, report.to_dict()
    )
    return tuple(f"{record.path}: {record.message}" for record in records)


__all__ = [
    "ABSENT_LOGS_REF",
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
    "build_logs_binding_layout",
    "build_report_document",
    "build_workspace_layout",
    "list_contract_violations",
    "read_document",
    "stage_artifact",
]
