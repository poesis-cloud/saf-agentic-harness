import json
import shlex


ADAPTER_ENV = "vscode-github-copilot-chat"
EXPECTED_WORKSPACE_HOOKS = {
    "SubagentStart": 60,
    "PreToolUse": 60,
    "PostToolUse": 60,
    "SubagentStop": 10,
    "Stop": 10,
}
EXPECTED_H0_ORCHESTRATORS = [
    "value-management-officer",
    "release-train-engineer",
    "scrum-master",
]


class TestHooksMap:
    def test_hooks_yaml_parses_as_copilot_hook_map(self, hooks_yaml):
        assert isinstance(hooks_yaml, dict)
        assert isinstance(hooks_yaml.get("hooks"), dict)

    def test_hooks_yaml_renders_like_makefile_install_hooks(self, hooks_yaml):
        rendered = json.dumps(hooks_yaml, indent=2)

        assert json.loads(rendered) == hooks_yaml
        assert rendered.startswith('{\n  "hooks":')

    def test_workspace_hooks_match_h1_through_h7_registrations(self, hooks_yaml):
        hooks = hooks_yaml["hooks"]

        assert set(hooks) == set(EXPECTED_WORKSPACE_HOOKS)
        for event, timeout in EXPECTED_WORKSPACE_HOOKS.items():
            entries = hooks[event]
            assert isinstance(entries, list)
            assert len(entries) == 1

            entry = entries[0]
            assert entry == {
                "type": "command",
                "command": f"adapters/dispatch.sh {event} {ADAPTER_ENV}",
                "cwd": "{{FRAMEWORK_DIR}}",
                "timeout": timeout,
            }
            assert shlex.split(entry["command"]) == [
                "adapters/dispatch.sh",
                event,
                ADAPTER_ENV,
            ]

    def test_h0_user_prompt_submit_is_agent_scoped_not_workspace_scoped(self, hooks_yaml):
        hooks = hooks_yaml["hooks"]

        assert "UserPromptSubmit" not in hooks
        for orchestrator_slug in EXPECTED_H0_ORCHESTRATORS:
            assert shlex.split(
                f"adapters/dispatch.sh UserPromptSubmit {ADAPTER_ENV} {orchestrator_slug}"
            ) == [
                "adapters/dispatch.sh",
                "UserPromptSubmit",
                ADAPTER_ENV,
                orchestrator_slug,
            ]

    def test_events_explicitly_not_registered_by_the_spec_are_absent(self, hooks_yaml):
        hooks = hooks_yaml["hooks"]

        assert "SessionStart" not in hooks
        assert "PreCompact" not in hooks
        assert "SessionEnd" not in hooks
        assert "ErrorOccurred" not in hooks

    def test_tool_classes_match_h2_through_h6_spec_sections(self, tools_yaml):
        assert tools_yaml["toolKeys"] == ["tool_name"]
        assert tools_yaml["inputKeys"] == ["tool_input"]
        assert tools_yaml["dispatchTools"] == ["runSubagent"]
        assert tools_yaml["dispatchAgentKeys"] == ["agentName"]
        assert tools_yaml["dispatchModelKeys"] == ["model"]
        assert tools_yaml["writeTools"] == {
            "create_file": "create",
            "create_directory": "create",
            "replace_string_in_file": "update",
            "multi_replace_string_in_file": "update",
            "edit_notebook_file": "update",
            "apply_patch": "update",
        }
        assert tools_yaml["deleteTools"] == []
        assert tools_yaml["pathKeys"] == ["filePath", "dirPath"]
        assert tools_yaml["nestedPathKeys"] == ["replacements[].filePath"]
        assert tools_yaml["mediatedCommandTools"] == ["run_in_terminal"]
        assert tools_yaml["mediatedCommandKeys"] == ["command"]
        assert tools_yaml["guardedShellTools"] == [
            "run_in_terminal",
            "create_and_run_task",
        ]

    def test_session_identity_binding_keys_match_spec(self, tools_yaml):
        assert tools_yaml["hostSessionKeys"] == ["session_id"]
        assert tools_yaml["hostParentSessionKeys"] == []
        assert tools_yaml["hostStepSessionKeys"] == ["agent_id"]
        assert tools_yaml["hostStepActorKeys"] == ["agent_type"]
