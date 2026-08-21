"""The write boundary's shared rig: a realistic nested workspace over two artifact kinds.

Spec (Workspace Git plane, principle 2): functions 8 and 9 are the write boundary — the
only plane that MUTATES the workspace — and both resolve the artifact kind FROM the write
path. So their fixtures bind artifacts through nested folder nodes carrying `<placeholder>`
segments, exactly as a real `conf/workspace.conf.yaml` does, and carry one
markdown-with-frontmatter kind beside one JSON kind: a flat, single-kind, JSON-only layout
cannot exercise either resolution.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from functional_fixtures import FunctionalHarness, build_capabilities, run_git

HarnessBuilder = Callable[..., FunctionalHarness]

BASE_ARTIFACT_CONTRACT_ID = "gsmarc://saf/contracts/artifact/v1"

EPIC_REF = "portfolio/payments/epics/checkout.epic.md"
OTHER_EPIC_REF = "portfolio/payments/epics/billing.epic.md"
FEATURE_REF = "portfolio/payments/features/refunds.feature.json"
OTHER_FEATURE_REF = "portfolio/billing/features/invoicing.feature.json"
UNBOUND_REF = "portfolio/payments/notes.md"
LOGS_REF = "logs/builder-session.log.jsonl"

# Nested folders + `<placeholder>` segments: the epic and feature kinds are only ever
# reachable through `portfolio/<portfolio-slug>/{epics,features}/<...>` — the shape a
# path -> kind resolver has to actually walk.
NESTED_WORKSPACE_LAYOUT: Mapping[str, Any] = {
    "nodes": [
        {
            "slug": "portfolio",
            "description": "The portfolio plane.",
            "children": [
                {
                    "slug": "<portfolio-slug>",
                    "description": "One portfolio.",
                    "children": [
                        {
                            "slug": "epics",
                            "description": "The portfolio's epics.",
                            "children": [
                                {
                                    "slug": "<epic-slug>.epic.md",
                                    "description": "One epic: markdown carrying frontmatter.",
                                    "cardinality": "0..*",
                                    "artifact": "epic",
                                    "template": "epic",
                                }
                            ],
                        },
                        {
                            "slug": "features",
                            "description": "The portfolio's features.",
                            "children": [
                                {
                                    "slug": "<feature-slug>.feature.json",
                                    "description": "One feature: a JSON document.",
                                    "cardinality": "0..*",
                                    "artifact": "feature",
                                    "template": "feature",
                                }
                            ],
                        },
                    ],
                }
            ],
        }
    ]
}

# The same nesting, with a second artifact kind whose pattern also binds `*.epic.md`: one
# path, two candidate kinds, and no `type` anywhere at the boundary to tell them apart.
AMBIGUOUS_WORKSPACE_LAYOUT: Mapping[str, Any] = {
    "nodes": [
        {
            "slug": "portfolio",
            "description": "The portfolio plane.",
            "children": [
                {
                    "slug": "<portfolio-slug>",
                    "description": "One portfolio.",
                    "children": [
                        {
                            "slug": "epics",
                            "description": "The portfolio's epics.",
                            "children": [
                                {
                                    "slug": "<epic-slug>.epic.md",
                                    "description": "One epic.",
                                    "cardinality": "0..*",
                                    "artifact": "epic",
                                    "template": "epic",
                                },
                                {
                                    "slug": "<any-slug>.epic.md",
                                    "description": "One enabler epic, same path pattern.",
                                    "cardinality": "0..*",
                                    "artifact": "feature",
                                    "template": "feature",
                                },
                            ],
                        }
                    ],
                }
            ],
        }
    ]
}

# `builder` holds every epic verb — `delete` included, so the unsupported-action deny is
# proved against a GRANTED delete rather than against a missing privilege.
WRITE_BOUNDARY_ACL: Mapping[str, Any] = {
    "actors": [
        {"slug": "orchestrator", "roles": ["facilitator"]},
        {"slug": "builder", "roles": ["epic-author"]},
        {"slug": "reviewer", "roles": ["feature-author"]},
    ],
    "roles": [
        {
            "slug": "facilitator",
            "privileges": [{"artifact": "epic", "action": "create"}],
        },
        {
            "slug": "epic-author",
            "privileges": [
                {"artifact": "epic", "action": "create"},
                {"artifact": "epic", "action": "update"},
                {"artifact": "epic", "action": "delete"},
            ],
        },
        {
            "slug": "feature-author",
            "privileges": [
                {"artifact": "feature", "action": "create"},
                {"artifact": "feature", "action": "update"},
            ],
        },
    ],
}

DELIVERY_WORKFLOW: Mapping[str, Any] = {
    "slug": "delivery",
    "orchestrator": "orchestrator",
    "skills": ["workflow-selection"],
    "instructions": ["workflow-selection-handling"],
    "steps": [
        {
            "slug": "draft-epic",
            "actor": "builder",
            "artifact": "epic",
            "instructions": ["draft-guidance"],
            "capabilities": build_capabilities(writing_quality=6),
        }
    ],
}


def _artifact_schema(slug: str) -> Mapping[str, Any]:
    """Build one fixture artifact schema extending the harness base contract.

    Spec (function 9, invariant 1 / `contracts/artifact.schema.json`): methodology
    artifact schemas extend the base via a TOP-LEVEL `$ref`, so the base's universal
    `slug` identity is in force alongside the kind's own constraints.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"gsmarc://saf/tests/functional/write-boundary/{slug}/v1",
        "$ref": BASE_ARTIFACT_CONTRACT_ID,
        "title": f"{slug} write-boundary fixture schema",
        "type": "object",
        "required": ["status"],
        "properties": {"status": {"type": "string", "enum": ["draft", "approved"]}},
        "additionalProperties": True,
    }


def install_artifact_schemas(harness: FunctionalHarness) -> None:
    """Replace the rig's generic fixture schemas with base-extending ones."""
    for slug in ("epic", "feature"):
        path = harness.framework_dir / "artifacts" / f"{slug}.artifact.schema.json"
        path.write_text(json.dumps(_artifact_schema(slug), indent=2), encoding="utf-8")


def build_write_boundary_harness(
    build_harness: HarnessBuilder,
    workspace_layout: Mapping[str, Any] | None = None,
) -> FunctionalHarness:
    """Assemble the nested write-boundary rig: layout, ACL, workflow, and schemas."""
    harness = build_harness(
        workflows={"delivery": DELIVERY_WORKFLOW},
        access_control_list=WRITE_BOUNDARY_ACL,
        workspace_layout=workspace_layout or NESTED_WORKSPACE_LAYOUT,
    )
    install_artifact_schemas(harness)
    return harness


def open_session(harness: FunctionalHarness, session_id: str, agent: str) -> str:
    """Register one agent's session and answer its id — function 8's actor source."""
    harness.invoke("start-session", sessionId=session_id, agent=agent)
    return session_id


def epic_markdown(slug: str, status: str) -> str:
    """Render one epic: YAML frontmatter its schema binds, plus prose no contract describes."""
    return f"---\nslug: {slug}\nstatus: {status}\n---\n\n# {slug}\n\nNarrative body.\n"


def feature_json(slug: str, status: str) -> str:
    """Render one feature as the JSON document its schema binds."""
    return json.dumps({"slug": slug, "status": status}, indent=2) + "\n"


def stage_write(harness: FunctionalHarness, ref: str, content: str) -> None:
    """Land one write in the working tree — the staging area, not workspace state."""
    path = harness.workspace_dir / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def commit_write(harness: FunctionalHarness, ref: str, content: str) -> None:
    """Promote one write into committed state — the baseline a later write starts from."""
    stage_write(harness, ref, content)
    run_git(harness.workspace_dir, "add", "--", ref)
    run_git(harness.workspace_dir, "commit", "-q", "-m", f"artifact: seed {ref}")


def read_worktree(harness: FunctionalHarness, ref: str) -> str | None:
    """Read one path's staged bytes, or none when the path does not exist."""
    path = harness.workspace_dir / ref
    return path.read_text(encoding="utf-8") if path.is_file() else None


def read_committed(harness: FunctionalHarness, ref: str) -> str:
    """Read one path's COMMITTED bytes — the only bytes that are workspace state (C0)."""
    return run_git(harness.workspace_dir, "show", f"HEAD:{ref}")


def read_last_commit_message(harness: FunctionalHarness) -> str:
    """Read the workspace repository's latest commit message."""
    return run_git(harness.workspace_dir, "log", "-1", "--pretty=%B")


def is_worktree_clean(harness: FunctionalHarness) -> bool:
    """Tell whether the working tree holds no staged or untracked artifact bytes."""
    status = run_git(
        harness.workspace_dir, "status", "--porcelain", "--untracked-files=all"
    )
    return status == ""


__all__ = [
    "AMBIGUOUS_WORKSPACE_LAYOUT",
    "BASE_ARTIFACT_CONTRACT_ID",
    "DELIVERY_WORKFLOW",
    "EPIC_REF",
    "FEATURE_REF",
    "LOGS_REF",
    "NESTED_WORKSPACE_LAYOUT",
    "OTHER_EPIC_REF",
    "OTHER_FEATURE_REF",
    "UNBOUND_REF",
    "WRITE_BOUNDARY_ACL",
    "build_write_boundary_harness",
    "commit_write",
    "epic_markdown",
    "feature_json",
    "install_artifact_schemas",
    "is_worktree_clean",
    "open_session",
    "read_committed",
    "read_last_commit_message",
    "read_worktree",
    "stage_write",
]
