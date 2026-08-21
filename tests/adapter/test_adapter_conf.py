import json


def _acl_action_vocabulary(repo_root) -> set:
    """Read the action verbs the harness contract admits, from the ACL schema itself."""
    schema = json.loads(
        (
            repo_root
            / "contracts"
            / "conf"
            / "framework"
            / "access-control-list.conf.schema.json"
        ).read_text(encoding="utf-8")
    )
    return set(schema["$defs"]["action"]["enum"])


class TestActionVocabulary:
    def test_every_action_this_binding_can_emit_is_in_the_acl_vocabulary(
        self, repo_root, tools_yaml
    ):
        """Adapter spec H3: "`action` maps from `tool_name` via `tools.yaml`
        (`writeTools` verb map; `deleteTools` -> `delete`)" — so the host-tool-to-action
        mapping is the ADAPTER's, and an action outside the harness vocabulary is
        adapter misconfiguration, caught here rather than at runtime. At runtime the
        harness input contract's `action` enum is the enforcement point: a bad value is
        an `invalid-inquiry`, which produces no report at all (core rule 4), so nothing
        downstream can diagnose it. The vocabulary is read from the ACL schema file, not
        restated, so this cannot pass against a stale copy of the verb list."""
        vocabulary = _acl_action_vocabulary(repo_root)
        emitted = set(tools_yaml["writeTools"].values())
        if tools_yaml.get("deleteTools"):
            emitted.add("delete")

        assert emitted
        assert emitted <= vocabulary, f"outside the ACL vocabulary: {emitted - vocabulary}"

    def test_the_delete_verb_deletetools_map_to_is_in_the_acl_vocabulary(
        self, repo_root
    ):
        """Adapter spec H3: `deleteTools` map to `delete` — a constant in the binding,
        not a configured value, so it stays unchecked by the test above while this
        binding declares no delete tools. Pinning it here means the day a delete tool is
        declared, the verb it emits is already known to be admissible."""
        assert "delete" in _acl_action_vocabulary(repo_root)

    def test_the_binding_schemas_own_action_enum_does_not_drift_from_the_acl(
        self, repo_root
    ):
        """Adapter spec (Boundary binding): the tool-class declaration is
        contract-governed. That contract restates the verb list inline in
        `writeTools`' enum — a copy, and copies drift. It must stay a SUBSET of the
        harness vocabulary, so a verb admitted at the adapter boundary is always one the
        harness itself admits."""
        schema = json.loads(
            (
                repo_root
                / "contracts"
                / "conf"
                / "adapters"
                / "tools.conf.schema.json"
            ).read_text(encoding="utf-8")
        )
        declared = set(
            schema["properties"]["writeTools"]["additionalProperties"]["enum"]
        )

        assert declared <= _acl_action_vocabulary(repo_root)


class TestAdapterConf:
    def test_models_yaml_validates_against_adapter_schema(
        self, repo_root, models_yaml, make_validator
    ):
        """Adapter spec (Model binding): the host's model catalog is contract-governed — the
        adapter's own configuration is validated like every other configuration source."""
        schema = json.loads(
            (
                repo_root
                / "contracts"
                / "conf"
                / "adapters"
                / "models.conf.schema.json"
            ).read_text(encoding="utf-8")
        )

        make_validator(schema).validate(models_yaml)

    def test_tools_yaml_validates_against_adapter_schema(
        self, repo_root, tools_yaml, make_validator
    ):
        """Adapter spec (Boundary binding): the tool-class declaration H2–H6 classify on is
        contract-governed — a malformed class map is a configuration error, not a runtime
        surprise inside a hook."""
        schema = json.loads(
            (
                repo_root
                / "contracts"
                / "conf"
                / "adapters"
                / "tools.conf.schema.json"
            ).read_text(encoding="utf-8")
        )

        make_validator(schema).validate(tools_yaml)

    def test_contract_registry_contains_gsmarc_schema_ids(self, contract_registry):
        """Adapter spec (Invocation plumbing): each of the adapter's four seams has its own
        registered contract — its two configuration sources and its stdin/stdout hook
        contracts — resolvable by `gsmarc://` id rather than by file path."""
        expected_ids = {
            "gsmarc://saf/contracts/conf/adapters/models.conf/v1",
            "gsmarc://saf/contracts/conf/adapters/tools.conf/v1",
            "gsmarc://saf/adapters/vscode-github-copilot-chat/contracts/hook-stdin/v1",
            "gsmarc://saf/adapters/vscode-github-copilot-chat/contracts/hook-stdout/v1",
        }

        assert expected_ids.issubset(str(uri) for uri in contract_registry)
