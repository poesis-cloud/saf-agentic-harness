"""Unit tests for `HookBinding` — the adapter's own declarative binding data."""

from __future__ import annotations

from pathlib import Path

import pytest

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
        """Adapter spec H3: a call naming no path reaches no artifact — nothing to
        authorize, hence pass-through material rather than a denial.
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
