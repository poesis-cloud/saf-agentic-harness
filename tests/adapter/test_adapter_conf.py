import json


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
