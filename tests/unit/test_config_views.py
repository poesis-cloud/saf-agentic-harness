"""Unit tests — the typed configuration views: AccessControlList, ModelProfiles,
WorkspaceLayout (one test class per src class)."""

from __future__ import annotations

from config import AccessControlList, ModelProfiles, WorkspaceLayout


class TestAccessControlList:
    ACL = {
        "actors": [
            {"id": "developer.agent.md", "roles": ["developer", "reader"]},
            {"id": "product-owner.agent.md", "roles": ["reader"]},
        ],
        "roles": [
            {"id": "developer", "privileges": [
                {"artifact": "story.artifact.schema.json", "action": "CREATE"},
                {"artifact": "story.artifact.schema.json", "action": "UPDATE"},
            ]},
            {"id": "reader", "privileges": [
                {"artifact": "epic.artifact.schema.json", "action": "READ"},
            ]},
        ],
    }

    def test_normalize_strips_handle_decorations(self):
        assert AccessControlList.normalize("@developer") == "developer"
        assert AccessControlList.normalize("developer.agent.md") == "developer"
        assert AccessControlList.normalize("agents/developer.agent.md") == "developer"
        assert AccessControlList.normalize("  @developer.agent.md ") == "developer"

    def test_agents_maps_each_actor_to_its_artifacts(self):
        agents = AccessControlList(self.ACL).agents()
        assert agents["developer"] == {"story.artifact.schema.json", "epic.artifact.schema.json"}
        assert agents["product-owner"] == {"epic.artifact.schema.json"}

    def test_privileges_unions_actions_across_roles(self):
        perms = AccessControlList(self.ACL).privileges("developer")
        assert perms is not None
        assert perms["actions"] == {"CREATE", "UPDATE", "READ"}

    def test_privileges_accepts_any_handle_shape(self):
        acl = AccessControlList(self.ACL)
        assert acl.privileges("@developer") == acl.privileges("developer.agent.md")

    def test_unknown_actor_has_no_privileges(self):
        assert AccessControlList(self.ACL).privileges("stranger") is None

    def test_unknown_role_reference_is_ignored(self):
        acl = AccessControlList({
            "actors": [{"id": "x.agent.md", "roles": ["ghost-role"]}],
            "roles": [],
        })
        assert acl.agents() == {}


class TestModelProfiles:
    CATALOG = {
        "models": [
            {"id": "big", "cost_rank": 9, "capability_scores": {"deep-reasoning": 10, "coding": 9}},
            {"id": "small", "cost_rank": 2, "capability_scores": {"deep-reasoning": 5, "fast-iteration": 9}},
        ]
    }

    def test_models_indexed_by_id(self):
        profiles = ModelProfiles(self.CATALOG)
        assert set(profiles.models()) == {"big", "small"}

    def test_is_known_model(self):
        profiles = ModelProfiles(self.CATALOG)
        assert profiles.is_known_model("big")
        assert not profiles.is_known_model("Auto")

    def test_capability_scores_coerced_to_float(self):
        scores = ModelProfiles(self.CATALOG).capability_scores("big")
        assert scores == {"deep-reasoning": 10.0, "coding": 9.0}
        assert all(isinstance(v, float) for v in scores.values())

    def test_capability_scores_of_unknown_model_are_empty(self):
        assert ModelProfiles(self.CATALOG).capability_scores("ghost") == {}

    def test_cost_rank_defaults_to_zero(self):
        profiles = ModelProfiles({"models": [{"id": "free"}]})
        assert profiles.cost_rank("free") == 0.0
        assert profiles.cost_rank("ghost") == 0.0

    def test_tags_is_the_union_of_all_scored_tags(self):
        assert ModelProfiles(self.CATALOG).tags() == {"deep-reasoning", "coding", "fast-iteration"}

    def test_entries_without_id_are_dropped(self):
        profiles = ModelProfiles({"models": [{"cost_rank": 5}, {"id": "ok"}]})
        assert set(profiles.models()) == {"ok"}


class TestWorkspaceLayout:
    LAYOUT = {
        "nodes": [
            {
                "path": "portfolio",
                "description": "root",
                "cardinality": "1",
                "children": [
                    {
                        "path": "portfolio-manifest.yaml",
                        "description": "manifest",
                        "cardinality": "1",
                        "schema": "portfolio-manifest.artifact.schema.json",
                    },
                    {
                        "path": "portfolio-backlog/<epic-slug>/<epic-slug>.epic.md",
                        "description": "epic",
                        "cardinality": "1..*",
                        "schema": "epic.artifact.schema.json",
                    },
                ],
            }
        ]
    }

    def test_walk_is_depth_first_over_all_nodes(self):
        paths = [node["path"] for node in WorkspaceLayout(self.LAYOUT).walk()]
        assert paths == [
            "portfolio",
            "portfolio-manifest.yaml",
            "portfolio-backlog/<epic-slug>/<epic-slug>.epic.md",
        ]

    def test_singleton_path_kind_selects_cardinality_one_schema_nodes(self):
        singletons = WorkspaceLayout(self.LAYOUT).singleton_path_kind()
        assert singletons == {"portfolio-manifest.yaml": "portfolio-manifest"}

    def test_schema_bindings_glob_slug_placeholders(self):
        bindings = WorkspaceLayout(self.LAYOUT).schema_bindings()
        assert bindings["portfolio-backlog/*/*.epic.md"] == "epic.artifact.schema.json"

    def test_empty_layout_yields_empty_views(self):
        layout = WorkspaceLayout({})
        assert layout.nodes() == []
        assert layout.singleton_path_kind() == {}
        assert layout.schema_bindings() == {}
