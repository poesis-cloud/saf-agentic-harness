"""Unit tests for ConfigLoader fail-fast source loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from errors import ConfigurationError
from tests.unit.config.conftest import (
    ambiguous_workspace_yaml,
    workflow_yaml,
    workspace_yaml,
    write_standard_configs,
    write_yaml,
)


class TestConfigLoader:
    """Verify ConfigLoader parses, validates, and builds typed views atomically."""

    def test_loads_layout_with_process_environment_precedence(self, config_loader, framework_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec: process environment overrides .env and FRAMEWORK_DIR defaults to .env parent."""
        (framework_root / "agents-ci").mkdir()
        monkeypatch.setenv("FRAMEWORK_AGENTS_DIR", "agents-ci")

        layout = config_loader.load_framework_layout(framework_root)

        assert layout.framework_dir == framework_root.resolve()
        assert layout.agents_dir == (framework_root / "agents-ci").resolve()
        assert layout.skills_dir == (framework_root / "skills").resolve()
        assert layout.schemas_dir == (framework_root / "artifacts").resolve()
        assert layout.workspace_dir == framework_root.parent / "workspace"

    def test_rejects_missing_required_environment_variable(self, config_loader, framework_root: Path) -> None:
        """Spec: every required layout variable is present before use."""
        (framework_root / ".env").write_text("FRAMEWORK_AGENTS_DIR=agents\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="FRAMEWORK_ARTIFACTS_DIR"):
            config_loader.load_framework_layout(framework_root)

    def test_rejects_nonexistent_environment_path(self, config_loader, framework_root: Path) -> None:
        """Spec: every declared layout path must exist."""
        (framework_root / "skills").rmdir()

        with pytest.raises(ConfigurationError, match="FRAMEWORK_SKILLS_DIR"):
            config_loader.load_framework_layout(framework_root)

    def test_rejects_schema_invalid_yaml_without_returning_partial_view(self, config_loader, framework_root: Path) -> None:
        """Spec: source parse and contract validation are one fail-fast act."""
        write_yaml(framework_root / "conf" / "model-profiles.conf.yaml", "modelProfiles: []\nextra: no\n")

        with pytest.raises(ConfigurationError, match="schema validation failed"):
            config_loader.load_model_profiles(framework_root)

    def test_rejects_unknown_workflow_property(self, config_loader, framework_root: Path) -> None:
        """Spec: workflow.conf additionalProperties is false — unknown keys fail fast."""
        write_standard_configs(framework_root)
        write_yaml(
            framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml",
            workflow_yaml() + "role: main\n",
        )

        with pytest.raises(ConfigurationError, match="schema validation failed"):
            config_loader.load_workflow_catalog(framework_root)

    def test_rejects_unknown_step_property(self, config_loader, framework_root: Path) -> None:
        """Spec: step additionalProperties is false — unknown keys fail fast."""
        write_standard_configs(framework_root)
        write_yaml(
            framework_root / "conf" / "workflows" / "planning.workflow.conf.yaml",
            workflow_yaml().replace("  - slug: draft\n", "  - slug: draft\n    role: main\n"),
        )

        with pytest.raises(ConfigurationError, match="schema validation failed"):
            config_loader.load_workflow_catalog(framework_root)

    def test_loads_all_configuration_sources_against_real_contracts(self, config_loader, framework_root: Path) -> None:
        """Spec: each load_* method returns an immutable typed configuration view."""
        write_standard_configs(framework_root)

        assert config_loader.load_access_control_list(framework_root).is_framework_agent("planner")
        assert "fast-coder" in config_loader.load_model_profiles(framework_root).profiles
        assert config_loader.load_workspace_layout(framework_root).resolve_resource(Path("portfolio/epics/a.epic.md")) == "epic"
        assert config_loader.load_workflow_catalog(framework_root).find_workflow("planning").slug == "planning"


class TestWorkspaceLayoutAmbiguityRule:
    """Verify a layout resolving one path to two artifact kinds is refused at load."""

    def test_rejects_sibling_leaves_claiming_one_path(self, config_loader, framework_root: Path) -> None:
        """Spec (Internal validation, configuration validity): `ConfigLoader` applies the
        semantic rules JSON Schema cannot express — a layout in which one path resolves to
        two artifact kinds is a framework authoring bug, not a runtime condition."""
        write_yaml(framework_root / "conf" / "workspace.conf.yaml", ambiguous_workspace_yaml())

        with pytest.raises(ConfigurationError) as failure:
            config_loader.load_workspace_layout(framework_root)

        assert failure.value.code == "ambiguous-workspace-path"
        assert "epic" in failure.value.message and "feature" in failure.value.message

    def test_accepts_leaves_separated_by_literal_suffixes(self, config_loader, framework_root: Path) -> None:
        """Spec (`workspace.conf.schema.json`): a slug mixes variable placeholders with
        literal text — literal suffixes separate two kinds under one folder, so the rule
        must accept them."""
        write_yaml(framework_root / "conf" / "workspace.conf.yaml", workspace_yaml())

        layout = config_loader.load_workspace_layout(framework_root)

        assert layout.resolve_resource(Path("portfolio/epics/a.epic.md")) == "epic"
        assert layout.resolve_resource(Path("portfolio/epics/a.feature.md")) == "feature"

    def test_accepts_the_real_framework_leaf_shape(self, config_loader, framework_root: Path) -> None:
        """Spec (`nodeSlug`): the same variable name recurs across a node and its
        descendants — the shipped framework's epic folder holds three kinds under one
        `<epic-slug>` folder, and the rule must not reject that shape."""
        write_yaml(
            framework_root / "conf" / "workspace.conf.yaml",
            """nodes:
  - slug: <epic-slug>
    description: Epic folder
    children:
      - slug: <epic-slug>.epic.md
        description: Epic
        cardinality: "1"
        artifact: epic
        template: epic
      - slug: <epic-slug>.epic-enabler.md
        description: Enabler epic
        cardinality: "1"
        artifact: epic-enabler
        template: epic-enabler
      - slug: <epic-slug>.lean-business-case.md
        description: Lean business case
        cardinality: "1"
        artifact: lean-business-case
        template: lean-business-case
""",
        )

        layout = config_loader.load_workspace_layout(framework_root)

        assert layout.resolve_resource(Path("pay/pay.epic.md")) == "epic"
        assert layout.resolve_resource(Path("pay/pay.epic-enabler.md")) == "epic-enabler"

    def test_rejects_a_collision_only_a_full_path_comparison_sees(self, config_loader, framework_root: Path) -> None:
        """Spec (`workspace.conf.schema.json`): the full workspace-relative path is the
        concatenation of ancestor slugs, so ambiguity is a property of whole paths — a
        literal folder and a variable folder collide across branches, not as siblings."""
        write_yaml(
            framework_root / "conf" / "workspace.conf.yaml",
            """nodes:
  - slug: epics
    description: Literal epic folder
    children:
      - slug: index.md
        description: Epic
        cardinality: "1"
        artifact: epic
        template: epic
  - slug: <any-slug>
    description: Variable folder
    children:
      - slug: index.md
        description: Feature
        cardinality: "1"
        artifact: feature
        template: feature
""",
        )

        with pytest.raises(ConfigurationError, match="ambiguous-workspace-path|epics/index.md"):
            config_loader.load_workspace_layout(framework_root)

    def test_rejects_an_overlap_no_suffix_comparison_would_find(self, config_loader, framework_root: Path) -> None:
        """Spec (Internal validation): the rule decides overlap of the slug language
        itself — `<a>-note.md` and `note-<b>.md` share no literal prefix or suffix yet
        both match `note-x-note.md`, so a suffix heuristic would silently admit them."""
        write_yaml(
            framework_root / "conf" / "workspace.conf.yaml",
            """nodes:
  - slug: portfolio
    description: Portfolio folder
    children:
      - slug: <a-slug>-note.md
        description: Epic
        cardinality: 0..*
        artifact: epic
        template: epic
      - slug: note-<b-slug>.md
        description: Feature
        cardinality: 0..*
        artifact: feature
        template: feature
""",
        )

        with pytest.raises(ConfigurationError) as failure:
            config_loader.load_workspace_layout(framework_root)

        assert failure.value.code == "ambiguous-workspace-path"

    def test_accepts_patterns_whose_literals_can_never_agree(self, config_loader, framework_root: Path) -> None:
        """Spec (Internal validation): over-rejection is a real cost — `pi-<slug>` and
        `art-<slug>` share a shape but no common string, and the rule must admit them."""
        write_yaml(
            framework_root / "conf" / "workspace.conf.yaml",
            """nodes:
  - slug: portfolio
    description: Portfolio folder
    children:
      - slug: pi-<pi-slug>
        description: PI folder
        children:
          - slug: plan.md
            description: Epic
            cardinality: "1"
            artifact: epic
            template: epic
      - slug: art-<art-slug>
        description: ART folder
        children:
          - slug: plan.md
            description: Feature
            cardinality: "1"
            artifact: feature
            template: feature
""",
        )

        layout = config_loader.load_workspace_layout(framework_root)

        assert layout.resolve_resource(Path("portfolio/pi-1/plan.md")) == "epic"
        assert layout.resolve_resource(Path("portfolio/art-1/plan.md")) == "feature"

    def test_accepts_leaves_lying_at_different_depths(self, config_loader, framework_root: Path) -> None:
        """Spec (`workspace.conf.schema.json`): a slug is one path segment and `/` belongs
        to no slug, so paths of unequal segment counts can never name one path — a variable
        folder must not be read as absorbing a deeper branch."""
        write_yaml(
            framework_root / "conf" / "workspace.conf.yaml",
            """nodes:
  - slug: portfolio
    description: Portfolio folder
    children:
      - slug: <item-slug>.md
        description: Epic
        cardinality: 0..*
        artifact: epic
        template: epic
      - slug: <folder-slug>
        description: Nested folder
        children:
          - slug: <item-slug>.md
            description: Feature
            cardinality: 0..*
            artifact: feature
            template: feature
""",
        )

        layout = config_loader.load_workspace_layout(framework_root)

        assert layout.resolve_resource(Path("portfolio/a.md")) == "epic"
        assert layout.resolve_resource(Path("portfolio/sub/a.md")) == "feature"

    def test_accepts_two_leaves_claiming_one_path_for_one_kind(self, config_loader, framework_root: Path) -> None:
        """Spec (function 8, invariant 2): the resource is the artifact's schema identity —
        two overlapping leaves bound to the SAME kind resolve one path to one resource, so
        they are not ambiguous."""
        write_yaml(
            framework_root / "conf" / "workspace.conf.yaml",
            """nodes:
  - slug: portfolio
    description: Portfolio folder
    children:
      - slug: <item-slug>.md
        description: Epic by variable
        cardinality: 0..*
        artifact: epic
        template: epic
      - slug: pinned.md
        description: The same kind, pinned
        cardinality: "1"
        artifact: epic
        template: epic
""",
        )

        layout = config_loader.load_workspace_layout(framework_root)

        assert layout.resolve_resource(Path("portfolio/pinned.md")) == "epic"

