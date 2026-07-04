"""Test section structure validation.

Uses the story schema discovered from the framework catalog so the suite stays
methodology-agnostic. The concrete artifact path is synthesized from the schema's
x-artifact.pathPatterns.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import SchemaCatalog
from mappers import Workspace
from models import Artifact
from utils import ArtifactValidator


def _schema_path_example(schemas: dict[str, dict], schema_id: str, variables: dict[str, str]) -> Path:
    """Synthesize a concrete on-disk path from the schema's pathPatterns."""
    schema = schemas[schema_id]
    patterns = schema.get("x-artifact", {}).get("pathPatterns") or []
    if not isinstance(patterns, list):
        patterns = [patterns]
    for pattern in patterns:
        candidate = pattern
        for key, value in variables.items():
            candidate = candidate.replace(f"{{{key}}}", value)
        parts = candidate.split("/")
        for i, part in enumerate(parts):
            if "*" in part:
                if i == 0:
                    parts[i] = part.replace("*", variables.get("product", "p"))
                else:
                    parts[i] = part.replace("*", variables.get("unit_id", "item-01"))
        candidate = "/".join(parts)
        if "{" not in candidate and "*" not in candidate:
            return Path(candidate)
    raise ValueError(f"could not synthesize a concrete path for {schema_id}")


def _story_fixture(tmp_path: Path, monkeypatch, story_id: str, body: str) -> tuple[Workspace, ArtifactValidator, Path]:
    """Build a workspace + validator + story path using the discovered story schema."""
    original_detect = Workspace.detect.__func__

    def detect_override(cls, framework_root=None, workspace_root=None):
        if workspace_root is None:
            workspace_root = tmp_path / "workspace"
        return original_detect(cls, framework_root, workspace_root)

    monkeypatch.setattr(Workspace, "detect", classmethod(detect_override))

    workspace = Workspace.detect()
    schemas = SchemaCatalog(workspace)
    validator = ArtifactValidator(workspace, schemas)
    raw_schemas = schemas.load_raw()
    story_schema = raw_schemas.get("story")
    assert story_schema, "story schema must be present in the framework catalog"

    story_path = tmp_path / "workspace" / _schema_path_example(raw_schemas, "story", {"product": "E1", "unit_id": story_id})
    story_path.parent.mkdir(parents=True, exist_ok=True)
    story_path.write_text(f"""---
id: {story_id}
title: {story_id.replace("-", " ").title()}
status: done
parent_feature: F1
type: user
work_item_relations: {{}}
sprint: 1
pi: Q1
adrs: []
driver: alice
navigator: bob
pair_swaps: []
estimate_points: 3
risk: low
complexity: simple
owner: alice
created: "2024-01-01"
open_items: []
cost:
  tokens_in: 0
  tokens_out: 0
  tokens_cached: 0
  tokens_self: 0
  tokens_rolled: 0
  dispatches: 0
  source: test
  committed: false
github:
  org: test
  repo: test
  issue: 1
---

{body}
""")

    artifact = Artifact(
        kind="story",
        path=story_path,
        fields={"id": story_id, "slug": story_id, "title": story_id.replace("-", " ").title(), "type": "user"},
        frontmatter="...",
    )
    return workspace, validator, artifact


def test_story_missing_acceptance_criteria(tmp_path, monkeypatch):
    """Story without Acceptance Criteria section raises error."""
    _, validator, artifact = _story_fixture(tmp_path, monkeypatch, "story-bad", "## Implementation Notes\n\nNo acceptance criteria section here!")

    report = validator.validate(artifact)
    # Section validation should flag missing required section
    section_errors = [f for f in report.findings if "Acceptance Criteria" in f.message and f.severity == "error"]
    assert section_errors, "Expected error for missing Acceptance Criteria in section validation"


def test_story_invalid_acceptance_criteria_format(tmp_path, monkeypatch):
    """Story with Acceptance Criteria but not as bullet list warns."""
    _, validator, artifact = _story_fixture(
        tmp_path, monkeypatch, "story-prose",
        "## Acceptance Criteria\n\nThis is prose form, not bullet points. Users should be able to reset their password via email and the system should send a reset link.",
    )

    report = validator.validate(artifact)
    # Should warn about pattern (prose instead of bullets)
    warnings = [f for f in report.findings if f.severity == "warning" and "bullet points" in f.message]
    assert warnings, "Expected warning about bullet list pattern for Acceptance Criteria"
