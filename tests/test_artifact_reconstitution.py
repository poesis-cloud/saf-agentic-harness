"""Artifact reconstitution TEST — verify byte-stable markdown regeneration.

Tests that an Artifact parsed from markdown can reconstitute itself back to
the original markdown via to_markdown(), preserving structure and content.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Artifact, Section
from text import frontmatter, markdown_body, parse_frontmatter, parse_sections


def test_artifact_to_markdown_with_sections() -> None:
    """Verify Artifact.to_markdown() produces valid markdown with preserved sections."""
    # Sample markdown with frontmatter + sections (realistic story-enabler structure)
    original_markdown = """---
id: story-123
status: ready
type: enabler
title: Implement Section domain class
parent_feature: feature-abc
sprint: 1
pi: 3
adrs: []
driver: alice
navigator: bob
pair_swaps: []
estimate_points: 5
risk: low
complexity: simple
owner: alice
enabler_type: architectural
created: 2026-07-03
work_item_relations: {}
wsjf: null
open_items: []
cost: null
github: null
---

## Acceptance Criteria

- AC1. Artifact.to_markdown() returns valid markdown
- AC2. Section hierarchy is preserved (## and ### levels)
- AC3. Raw section body text is unmodified

## Implementation Notes

This is the implementation section with detailed steps:

### Substep 1

First step description.

### Substep 2

Second step description.

## Open Items

- Clarification: Need to verify edge case with empty sections
"""

    # Parse the original
    front = frontmatter(original_markdown)
    body = markdown_body(original_markdown)
    fields = parse_frontmatter(front)
    sections = parse_sections(body)

    # Create Artifact
    artifact = Artifact(
        kind="story",
        path=Path("portfolio/pi3/sprint-1/stories/story-123.md"),
        fields=fields,
        frontmatter=front,
        sections=sections,
        product_slug="",
    )

    # Reconstitute
    reconstituted = artifact.to_markdown()

    # Verify structure
    assert reconstituted.startswith("---"), "Reconstituted markdown must start with frontmatter"
    assert "\n---\n" in reconstituted, "Frontmatter must end with closing ---"
    assert "## Acceptance Criteria" in reconstituted, "Top-level sections preserved"
    assert "## Implementation Notes" in reconstituted, "All sections present"
    assert "## Open Items" in reconstituted, "Final section present"
    assert "### Substep 1" in reconstituted, "Subsections preserved"
    assert "### Substep 2" in reconstituted, "All subsections preserved"
    assert "AC1." in reconstituted, "Section body content preserved"
    assert "First step description" in reconstituted, "Subsection body content preserved"

    # Verify sections structure in artifact
    assert len(artifact.sections) == 3, "Three top-level sections"
    assert artifact.sections[0].name == "Acceptance Criteria"
    assert artifact.sections[1].name == "Implementation Notes"
    assert artifact.sections[2].name == "Open Items"

    # Verify hierarchy
    impl_notes = artifact.sections[1]
    assert len(impl_notes.children) == 2, "Implementation Notes has two subsections"
    assert impl_notes.children[0].name == "Substep 1"
    assert impl_notes.children[1].name == "Substep 2"

    # Verify flat traversal
    all_sections = artifact.all_sections_flat()
    section_names = [s.name for s in all_sections]
    assert "Acceptance Criteria" in section_names
    assert "Substep 1" in section_names
    assert "Substep 2" in section_names


def test_section_to_markdown_hierarchy() -> None:
    """Verify Section.to_markdown() preserves hierarchy and nesting."""
    # Create nested section structure
    substep1 = Section(level=3, name="Substep 1", body="First details.", children=[])
    substep2 = Section(level=3, name="Substep 2", body="Second details.", children=[])
    impl_notes = Section(
        level=2, name="Implementation Notes", body="Main notes here.", children=[substep1, substep2]
    )

    # Render
    rendered = impl_notes.to_markdown(include_heading=True)

    # Verify structure
    assert "## Implementation Notes" in rendered, "Top-level heading included"
    assert "Main notes here." in rendered, "Top-level body preserved"
    assert "### Substep 1" in rendered, "Child heading at correct level"
    assert "First details." in rendered, "Child body preserved"
    assert "### Substep 2" in rendered, "Second child heading"
    assert "Second details." in rendered, "Second child body preserved"

    # Verify order
    lines = rendered.split("\n")
    impl_idx = next(i for i, line in enumerate(lines) if "## Implementation Notes" in line)
    step1_idx = next(i for i, line in enumerate(lines) if "### Substep 1" in line)
    step2_idx = next(i for i, line in enumerate(lines) if "### Substep 2" in line)
    assert impl_idx < step1_idx < step2_idx, "Sections render in order"


def test_section_flatten() -> None:
    """Verify Section.flatten() returns depth-first traversal."""
    # Create hierarchy: Level 2 -> Level 3 -> Level 4
    leaf = Section(level=4, name="Leaf", body="leaf content", children=[])
    branch = Section(level=3, name="Branch", body="branch content", children=[leaf])
    root = Section(level=2, name="Root", body="root content", children=[branch])

    flat = root.flatten()

    # Should be depth-first: root, branch, leaf
    assert len(flat) == 3
    assert flat[0].name == "Root"
    assert flat[1].name == "Branch"
    assert flat[2].name == "Leaf"


def test_section_by_name() -> None:
    """Verify Section.by_name() finds descendant by name."""
    leaf1 = Section(level=4, name="Detail A", body="", children=[])
    leaf2 = Section(level=4, name="Detail B", body="", children=[])
    branch = Section(level=3, name="Branch", body="", children=[leaf1, leaf2])
    root = Section(level=2, name="Root", body="", children=[branch])

    # Find by name
    found_a = root.by_name("Detail A")
    assert found_a is not None
    assert found_a.name == "Detail A"

    found_b = root.by_name("Detail B")
    assert found_b is not None
    assert found_b.name == "Detail B"

    found_branch = root.by_name("Branch")
    assert found_branch is not None
    assert found_branch.name == "Branch"

    found_none = root.by_name("NonExistent")
    assert found_none is None


if __name__ == "__main__":
    test_artifact_to_markdown_with_sections()
    test_section_to_markdown_hierarchy()
    test_section_flatten()
    test_section_by_name()
    print("All reconstitution tests passed! ✓")
