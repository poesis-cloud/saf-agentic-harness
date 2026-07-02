"""Test section structure validation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mappers import SchemaMapper, Workspace
from models import Artifact
from utils import ArtifactValidator


def test_story_missing_acceptance_criteria(tmp_path, monkeypatch):
    """Story without Acceptance Criteria section raises error."""
    original_detect = Workspace.detect.__func__

    def detect_override(cls, framework_root=None, portfolio_root=None):
        if portfolio_root is None:
            portfolio_root = tmp_path / "portfolio"
        return original_detect(cls, framework_root, portfolio_root)

    monkeypatch.setattr(Workspace, "detect", classmethod(detect_override))

    workspace = Workspace.detect()
    schemas = SchemaMapper(workspace)
    validator = ArtifactValidator(workspace, schemas)

    story_path = tmp_path / "portfolio" / "epics" / "E1" / "features" / "F1" / "sprint-1" / "stories" / "story-bad.md"
    story_path.parent.mkdir(parents=True, exist_ok=True)
    story_path.write_text("""---
id: story-bad
title: Missing AC
status: done
parent_feature: F1
type: user
work_item_relations: {}
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

## Implementation Notes

No acceptance criteria section here!
""")

    artifact = Artifact(
        kind="story",
        path=story_path,
        fields={"id": "story-bad", "title": "Missing AC", "type": "user"},
        frontmatter="...",
    )

    report = validator.validate(artifact)
    # Section validation should flag missing required section
    section_errors = [f for f in report.findings if "Acceptance Criteria" in f.message and f.severity == "error"]
    assert section_errors, "Expected error for missing Acceptance Criteria in section validation"


def test_story_invalid_acceptance_criteria_format(tmp_path, monkeypatch):
    """Story with Acceptance Criteria but not as bullet list warns."""
    original_detect = Workspace.detect.__func__

    def detect_override(cls, framework_root=None, portfolio_root=None):
        if portfolio_root is None:
            portfolio_root = tmp_path / "portfolio"
        return original_detect(cls, framework_root, portfolio_root)

    monkeypatch.setattr(Workspace, "detect", classmethod(detect_override))

    workspace = Workspace.detect()
    schemas = SchemaMapper(workspace)
    validator = ArtifactValidator(workspace, schemas)

    story_path = tmp_path / "portfolio" / "epics" / "E1" / "features" / "F1" / "sprint-1" / "stories" / "story-prose.md"
    story_path.parent.mkdir(parents=True, exist_ok=True)
    story_path.write_text("""---
id: story-prose
title: Prose AC
status: done
parent_feature: F1
type: user
work_item_relations: {}
sprint: 1
pi: Q1
adrs: []
driver: alice
navigator: bob
pair_swaps: []
estimate_points: 5
risk: medium
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
  issue: 2
---

## Acceptance Criteria

This is prose form, not bullet points. Users should be able to reset their password via email and the system should send a reset link.
""")

    artifact = Artifact(
        kind="story",
        path=story_path,
        fields={"id": "story-prose", "title": "Prose AC", "type": "user"},
        frontmatter="...",
    )

    report = validator.validate(artifact)
    # Should warn about pattern (prose instead of bullets)
    warnings = [f for f in report.findings if f.severity == "warning" and "bullet points" in f.message]
    assert warnings, "Expected warning about bullet list pattern for Acceptance Criteria"
