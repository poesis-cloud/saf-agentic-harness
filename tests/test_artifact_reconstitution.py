"""Artifact reconstitution TEST — verify markdown round-trip fidelity.

Tests that an Artifact parsed from markdown can reconstitute itself back to
the original markdown via to_markdown(), preserving structure and content.

Uses the framework's own artifact template (story.artifact-template.md) as the
test fixture. Templates live under layers/team/actors/.../artifacts/ and are
stable, versioned framework assets — unlike portfolio/ content, which is
volatile instance data owned by other actors and not a reliable test anchor.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from models import Artifact, Section
from text import frontmatter, markdown_body, parse_frontmatter, parse_sections, extract_file_heading

_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "layers" / "team" / "actors" / "product-owner" / "artifacts" / "story.artifact-template.md"
)

_FENCE_PATTERN = re.compile(r"```markdown\n(.*?)\n```", re.DOTALL)


def _extract_template_body(template_text: str) -> str:
    """Extract the fenced ```markdown ... ``` block from an artifact template doc.

    Artifact templates wrap the actual instance-shaped markdown (frontmatter +
    body) inside a fenced code block, surrounded by authoring guidance. Only
    the fenced block is a real markdown artifact instance and thus a valid
    round-trip fixture.
    """
    match = _FENCE_PATTERN.search(template_text)
    assert match, f"No ```markdown fenced block found in {_TEMPLATE_PATH}"
    return match.group(1) + "\n"


def _file_signature(content: str) -> str:
    """Compute SHA256 signature of markdown content (normalized for comparison)."""
    # Normalize: strip trailing whitespace from each line, ensure single newline at EOF
    normalized = "\n".join(line.rstrip() for line in content.split("\n"))
    if not normalized.endswith("\n"):
        normalized += "\n"
    return hashlib.sha256(normalized.encode()).hexdigest()


def test_artifact_to_markdown_real_template() -> None:
    """Verify Artifact.to_markdown() produces stable output matching the framework template.

    Uses the framework's own story.artifact-template.md (the fenced example
    block) as fixture. This tests true round-trip fidelity: load → parse →
    reconstitute → compare signature, anchored on a stable framework asset
    rather than volatile portfolio instance data.
    """
    assert _TEMPLATE_PATH.exists(), f"Framework template not found: {_TEMPLATE_PATH}"

    original_content = _extract_template_body(_TEMPLATE_PATH.read_text())

    # Parse the artifact
    front = frontmatter(original_content)
    body = markdown_body(original_content)
    fields = parse_frontmatter(front)
    sections = parse_sections(body)

    artifact = Artifact(
        kind="story",
        path=_TEMPLATE_PATH,
        fields=fields,
        frontmatter=front,
        sections=sections,
        product_slug="",
        heading=extract_file_heading(body),
    )

    # Reconstitute
    reconstituted = artifact.to_markdown()

    # Compare signatures (normalized to handle whitespace variations)
    original_sig = _file_signature(original_content)
    reconstituted_sig = _file_signature(reconstituted)

    # Signatures should match (byte-stable round-trip)
    assert original_sig == reconstituted_sig, (
        f"Artifact round-trip mismatch:\n"
        f"  Original:      {original_sig}\n"
        f"  Reconstituted: {reconstituted_sig}\n"
        f"  Original had {len(sections)} top-level sections\n"
        f"  Reconstituted has {len(artifact.sections)} top-level sections"
    )

    # Verify frontmatter preserved
    assert artifact.fields.get("id") == fields.get("id"), "ID field must be preserved"
    assert artifact.fields.get("title") == fields.get("title"), "Title field must be preserved"

    # Verify section count matches
    assert len(artifact.sections) == len(sections), "Section count must be preserved"


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
