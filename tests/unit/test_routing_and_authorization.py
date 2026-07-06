"""Unit tests — the routing + authorization services: ModelRouter, AuthorizationPolicy
(one test class per src class)."""

from __future__ import annotations

from config import AccessControlList, ModelProfiles, WorkspaceLayout
from services import ModelRouter


def _profiles(*entries) -> ModelProfiles:
    return ModelProfiles({"models": list(entries)})


class TestModelRouter:
    def test_resolve_picks_highest_weighted_score(self):
        router = ModelRouter(_profiles(
            {"id": "strong", "cost_rank": 8, "capability_scores": {"deep-reasoning": 9}},
            {"id": "weak", "cost_rank": 1, "capability_scores": {"deep-reasoning": 4}},
        ))
        resolved = router.resolve({"deep-reasoning": 5})
        assert resolved["model"] == "strong"
        assert resolved["score"] == 45.0
        assert "deep-reasoning" in resolved["reason"]

    def test_score_is_the_weighted_sum_over_step_tags_only(self):
        router = ModelRouter(_profiles(
            {"id": "m", "cost_rank": 5, "capability_scores": {"coding": 8, "deep-reasoning": 6, "multimodal": 10}},
        ))
        assert router.score("m", {"coding": 3, "deep-reasoning": 2}) == 36.0
        assert router.score("m", {"coding": 3}) == 24.0
        assert router.score("m", {"unknown-tag": 10}) == 0.0

    def test_tie_breaks_toward_lower_cost_rank(self):
        router = ModelRouter(_profiles(
            {"id": "pricey", "cost_rank": 9, "capability_scores": {"coding": 7}},
            {"id": "cheap", "cost_rank": 2, "capability_scores": {"coding": 7}},
        ))
        assert router.resolve({"coding": 5})["model"] == "cheap"

    def test_zero_weights_are_excluded_from_scoring_and_reason(self):
        router = ModelRouter(_profiles(
            {"id": "coder", "cost_rank": 5, "capability_scores": {"coding": 9, "writing-quality": 1}},
            {"id": "writer", "cost_rank": 5, "capability_scores": {"coding": 1, "writing-quality": 9}},
        ))
        resolved = router.resolve({"coding": 5, "writing-quality": 0})
        assert resolved["model"] == "coder"
        assert "writing-quality" not in resolved["reason"]

    def test_all_zero_weights_is_unroutable(self):
        router = ModelRouter(_profiles({"id": "m", "cost_rank": 1, "capability_scores": {"coding": 5}}))
        assert router.resolve({"coding": 0}) is None
        assert router.resolve({}) is None
        assert router.resolve(None) is None

    def test_empty_catalog_is_unroutable(self):
        assert ModelRouter(_profiles()).resolve({"coding": 5}) is None

    def test_validate_dispatch_rejects_auto_omitted_and_unknown(self):
        router = ModelRouter(_profiles({"id": "known", "cost_rank": 1, "capability_scores": {}}))
        assert router.validate_dispatch(None) is not None
        assert router.validate_dispatch("") is not None
        assert router.validate_dispatch("Auto") is not None
        assert router.validate_dispatch("auto") is not None
        assert router.validate_dispatch("ghost") is not None
        assert router.validate_dispatch("known") is None

    def test_is_known_model_delegates_to_catalog(self):
        router = ModelRouter(_profiles({"id": "known", "cost_rank": 1, "capability_scores": {}}))
        assert router.is_known_model("known")
        assert not router.is_known_model("ghost")


class TestAccessControlListAuthorization:
    """The authorization query lives on the ACL config view (AuthorizationPolicy dissolved)."""

    ACL = AccessControlList({
        "actors": [{"id": "developer.agent.md", "roles": ["developer"]}],
        "roles": [{"id": "developer", "privileges": [
            {"artifact": "story.artifact.schema.json", "action": "CREATE"},
            {"artifact": "story.artifact.schema.json", "action": "READ"},
        ]}],
    })

    def test_allows_granted_action_on_granted_resource(self):
        assert self.ACL.allows("developer", "create", "story") is True
        assert self.ACL.allows("developer", "CREATE", "story.artifact.schema.json") is True

    def test_denies_ungranted_action(self):
        assert self.ACL.allows("developer", "delete", "story") is False

    def test_denies_ungranted_resource(self):
        assert self.ACL.allows("developer", "create", "epic") is False

    def test_denies_unknown_actor(self):
        assert self.ACL.allows("stranger", "create", "story") is False

    def test_actor_handle_shapes_are_normalized(self):
        assert self.ACL.allows("@developer", "create", "story") is True
        assert self.ACL.allows("developer.agent.md", "create", "story") is True

    def test_resource_aliases_bridge_schema_filename_and_stem(self):
        aliases = AccessControlList._resource_aliases("story")
        assert aliases == {"story", "story.artifact.schema.json"}
        aliases = AccessControlList._resource_aliases("story.artifact.schema.json")
        assert "story" in aliases


class TestWorkspaceLayoutSingletons:
    """singleton_kind lives on the workspace layout view (AuthorizationPolicy dissolved)."""

    def test_singleton_kind_matches_glob_patterns(self):
        layout = WorkspaceLayout({"nodes": [
            {"path": "art", "description": "arts", "cardinality": "1..*", "children": [
                {"path": "art/<art-slug>/art-manifest.yaml", "description": "m",
                 "cardinality": "1", "schema": "art-manifest.artifact.schema.json"},
            ]},
        ]})
        assert layout.singleton_kind("art/my-art/art-manifest.yaml") == "art-manifest"
        assert layout.singleton_kind("art/my-art/other.yaml") is None
