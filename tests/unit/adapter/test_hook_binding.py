"""Unit tests for `HookBinding` — the adapter's own declarative binding data."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from binding_contract import BindingContractError
from hook_binding import HookBinding, load_hook_binding


@pytest.fixture
def binding(adapter_dir: Path) -> HookBinding:
    return load_hook_binding(adapter_dir)


class TestHookBinding:
    """The binding view over this adapter's own `tools.yaml` / `models.yaml`."""

    def test_loads_the_shipped_binding_with_its_own_tools(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec I15: the binding is loaded by the adapter's OWN tools, never
        through the harness `ConfigLoader` — the adapter depends on the command API only.
        """
        assert binding.dispatch_tools == ("runSubagent",)
        assert binding.mediated_command_tools == ("run_in_terminal",)
        assert binding.guarded_shell_tools == ("run_in_terminal", "create_and_run_task")
        assert binding.write_tools["create_file"] == "create"
        assert binding.path_keys == ("filePath", "dirPath")
        assert binding.nested_path_keys == ("replacements[].filePath",)

    def test_resolves_the_write_action_of_every_declared_write_tool(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec H3: `action` maps from `tool_name` via `tools.yaml`'s write-verb
        map; a tool outside the map has no write action.
        """
        assert binding.resolve_write_action("create_file") == "create"
        assert binding.resolve_write_action("create_directory") == "create"
        assert binding.resolve_write_action("replace_string_in_file") == "update"
        assert binding.resolve_write_action("apply_patch") == "update"
        assert binding.resolve_write_action("runSubagent") is None

    def test_resolves_the_delete_action_of_a_declared_delete_tool(self) -> None:
        """Adapter spec H3: `deleteTools -> delete` (this binding declares none, and the
        core denies every delete in v1 anyway — the mapping still has to exist).
        """
        binding = HookBinding(
            write_tools={},
            delete_tools=("delete_file",),
            dispatch_tools=(),
            mediated_command_tools=(),
            guarded_shell_tools=(),
            tool_keys=("tool_name",),
            input_keys=("tool_input",),
            path_keys=("filePath",),
            nested_path_keys=(),
            host_session_keys=("session_id",),
            host_step_session_keys=("agent_id",),
            host_step_actor_keys=("agent_type",),
            dispatch_agent_keys=(),
            dispatch_model_keys=(),
            mediated_command_keys=("command",),
            model_ids={},
        )

        assert binding.resolve_write_action("delete_file") == "delete"

    def test_extracts_flat_artifact_paths_per_path_keys(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec H3: path extraction probes `tool_input` per `pathKeys`
        (`filePath`, `dirPath`).
        """
        assert binding.extract_artifact_paths(
            {"filePath": "portfolio/epics/epic-payments.md", "content": "…"}
        ) == (Path("portfolio/epics/epic-payments.md"),)
        assert binding.extract_artifact_paths({"dirPath": "portfolio/payments"}) == (
            Path("portfolio/payments"),
        )

    def test_extracts_nested_replacement_paths_and_deduplicates_them(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec H3 / I8: `multi_replace_string_in_file` yields
        `replacements[].filePath` — one invocation per DISTINCT path.
        """
        paths = binding.extract_artifact_paths(
            {
                "replacements": [
                    {"filePath": "portfolio/a.md", "oldString": "x"},
                    {"filePath": "portfolio/b.md", "oldString": "y"},
                    {"filePath": "portfolio/a.md", "oldString": "z"},
                ]
            }
        )

        assert paths == (Path("portfolio/a.md"), Path("portfolio/b.md"))

    def test_extracts_no_path_from_a_call_naming_none(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec H3: extraction itself never invents a path — whether an empty
        extraction passes through or fails closed is the adapter's decision (H3/H5).
        """
        assert binding.extract_artifact_paths({"command": "ls"}) == ()

    def test_resolves_the_host_model_id_of_a_canonical_slug(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec I12: `models.yaml` is the only place naming the exact
        `"Model Name (copilot)"` string this host expects for `runSubagent`.
        """
        assert binding.resolve_model_id("claude-sonnet-4.6") == "Claude Sonnet 4.6 (copilot)"
        assert binding.resolve_model_id("no-such-model") is None

    def test_probes_a_payload_value_first_hit_wins(self, binding: HookBinding) -> None:
        """Adapter spec (tools.yaml header): the `*Keys` lists name the payload fields to
        probe — first hit wins.
        """
        payload = {"agent_id": "sub-1", "agent_type": "qa-engineer"}

        assert binding.probe_payload_value(payload, binding.host_step_session_keys) == "sub-1"
        assert binding.probe_payload_value(payload, binding.host_step_actor_keys) == "qa-engineer"
        assert binding.probe_payload_value(payload, ("missing",)) is None

    def test_carries_guarded_path_markers_it_was_constructed_with(
        self, adapter_dir: Path
    ) -> None:
        """Adapter spec H4 rule 1 / I9: the guarded-shell check needs the workspace
        artifact-layout and logs path markers — injected, never read from harness config
        (I15).
        """
        binding = load_hook_binding(adapter_dir, guarded_path_markers=("portfolio/", "logs/"))

        assert binding.guarded_path_markers == ("portfolio/", "logs/")


def _write_binding(
    target: Path,
    source: Path,
    tools_overrides: dict | None = None,
    models_overrides: dict | None = None,
    drop_tools_keys: tuple[str, ...] = (),
) -> Path:
    """Lay out an adapter dir holding the shipped binding with targeted mutations."""
    tools = yaml.safe_load((source / "tools.yaml").read_text(encoding="utf-8"))
    models = yaml.safe_load((source / "models.yaml").read_text(encoding="utf-8"))
    tools.update(tools_overrides or {})
    for key in drop_tools_keys:
        tools.pop(key, None)
    models.update(models_overrides or {})
    target.mkdir(parents=True, exist_ok=True)
    (target / "tools.yaml").write_text(yaml.safe_dump(tools), encoding="utf-8")
    (target / "models.yaml").write_text(yaml.safe_dump(models), encoding="utf-8")
    return target


class TestHookBindingFailFastValidation:
    """Adapter spec (Invocation plumbing — the binding is validated at load like any other
    configuration): a malformed binding stops the hook; it never degrades into empty
    probes, which would classify every firing as a pass-through — a silent fail-open."""

    @pytest.mark.parametrize(
        ("overrides", "dropped"),
        [
            ({"writeTools": []}, ()),
            ({"writeTools": {"create_file": "delete"}}, ()),
            ({"pathKeys": "filePath"}, ()),
            ({"nestedPathKeys": ["replacements[.filePath"]}, ()),
            ({"unknownKey": ["x"]}, ()),
            ({}, ("toolKeys",)),
            ({}, ("hostSessionKeys",)),
        ],
    )
    def test_refuses_a_tools_binding_its_contract_rejects(
        self, tmp_path: Path, adapter_dir: Path, overrides: dict, dropped: tuple
    ) -> None:
        bad = _write_binding(
            tmp_path / "bad", adapter_dir, tools_overrides=overrides, drop_tools_keys=dropped
        )

        with pytest.raises(BindingContractError, match="tools.conf.schema.json"):
            load_hook_binding(bad)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"toolKeys": []},
            {"inputKeys": []},
            {"hostSessionKeys": []},
            {"pathKeys": [], "nestedPathKeys": [], "patchKeys": []},
            {"mediatedCommandKeys": []},
        ],
    )
    def test_refuses_an_empty_probe_list_a_declared_gate_depends_on(
        self, tmp_path: Path, adapter_dir: Path, overrides: dict
    ) -> None:
        bad = _write_binding(tmp_path / "bad", adapter_dir, tools_overrides=overrides)

        with pytest.raises(BindingContractError, match="declares no"):
            load_hook_binding(bad)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"modelBindings": {}},
            {"modelBindings": {"gpt-5.5": {}}},
            {"modelBindings": {"gpt-5.5": {"hostModelId": ""}}},
        ],
    )
    def test_refuses_a_models_binding_its_contract_rejects(
        self, tmp_path: Path, adapter_dir: Path, overrides: dict
    ) -> None:
        bad = _write_binding(tmp_path / "bad", adapter_dir, models_overrides=overrides)

        with pytest.raises(BindingContractError, match="models.conf.schema.json"):
            load_hook_binding(bad)

    def test_refuses_an_unparsable_binding_file(
        self, tmp_path: Path, adapter_dir: Path
    ) -> None:
        bad = _write_binding(tmp_path / "bad", adapter_dir)
        (bad / "tools.yaml").write_text("writeTools: [unclosed\n", encoding="utf-8")

        with pytest.raises(BindingContractError, match="cannot be loaded"):
            load_hook_binding(bad)

    def test_the_shared_validator_depends_on_no_harness_core_package(
        self, repo_root: Path
    ) -> None:
        """Adapter spec I15: the shared validator any adapter imports stays outside the
        harness core — PyYAML and jsonschema only."""
        code = (repo_root / "adapters" / "binding_contract.py").read_text(encoding="utf-8")
        for forbidden in ("services", "stores", "config", "errors", "utils"):
            assert f"import {forbidden}" not in code
            assert f"from {forbidden}" not in code


_PATCH = """*** Begin Patch
*** Add File: portfolio/new.md
+# New
*** Update File: portfolio/kept.md
@@
-old
+new
*** Update File: portfolio/old-name.md
*** Move to: portfolio/new-name.md
@@
-a
+b
*** Delete File: portfolio/gone.md
*** End Patch
"""


class TestHookBindingPatchExtraction:
    """Paths a patch-document tool (`apply_patch`) names only in its patch text."""

    def test_loads_the_shipped_patch_probe(self, binding: HookBinding) -> None:
        """Adapter spec I8: `apply_patch` carries its paths in a patch document under
        `input` — the shipped binding declares that probe.
        """
        assert binding.patch_keys == ("input",)

    def test_extracts_every_path_a_patch_document_names(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec H3 / I8: every `*** Add File:` / `*** Update File:` /
        `*** Delete File:` / `*** Move to:` header names an artifact path, in call order.
        """
        assert binding.extract_artifact_paths({"input": _PATCH}) == (
            Path("portfolio/new.md"),
            Path("portfolio/kept.md"),
            Path("portfolio/old-name.md"),
            Path("portfolio/new-name.md"),
            Path("portfolio/gone.md"),
        )

    def test_resolves_the_action_each_patch_header_performs(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec H3: a patch header carries its own action — an added file is a
        `create`, an updated one an `update`, a deleted one a `delete`, and a move vacates
        its source (`delete`) and creates its destination (`create`).
        """
        assert binding.resolve_path_actions("apply_patch", {"input": _PATCH}) == (
            (Path("portfolio/new.md"), "create"),
            (Path("portfolio/kept.md"), "update"),
            (Path("portfolio/old-name.md"), "delete"),
            (Path("portfolio/new-name.md"), "create"),
            (Path("portfolio/gone.md"), "delete"),
        )

    def test_resolves_the_tool_action_of_every_flat_path(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec H3: a path reached by a declared key takes its tool's action."""
        assert binding.resolve_path_actions(
            "replace_string_in_file", {"filePath": "portfolio/a.md"}
        ) == ((Path("portfolio/a.md"), "update"),)

    def test_extracts_no_path_from_patch_text_naming_none(
        self, binding: HookBinding
    ) -> None:
        """Adapter spec H3: a patch with no file header names nothing (the adapter then
        fails closed — it never guesses a path).
        """
        assert binding.extract_artifact_paths({"input": "no path here"}) == ()
        assert binding.resolve_path_actions("apply_patch", {"input": "no path here"}) == ()
