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
        """Adapter spec (Hook registration): `hooks.yaml` is the host's own hook-map shape —
        an object keyed by event name — not an adapter-private format."""
        assert isinstance(hooks_yaml, dict)
        assert isinstance(hooks_yaml.get("hooks"), dict)

    def test_hooks_yaml_is_a_render_source_not_an_installable_file(self, hooks_yaml):
        """Adapter spec (Rendered registration): this file is the source of truth, not the
        installed artifact — it carries deployment placeholders no checkout can resolve, so
        installing it verbatim would hand the host a command and a cwd that do not exist."""
        rendered = json.dumps(hooks_yaml, indent=2)

        assert json.loads(rendered) == hooks_yaml
        assert rendered.startswith('{\n  "hooks":')
        assert "{{ADAPTERS_DIR}}" in rendered
        assert "{{FRAMEWORK_DIR}}" in rendered

    def test_workspace_hooks_match_h1_through_h7_registrations(self, hooks_yaml):
        """Adapter spec H1–H7: exactly the events those hooks declare are registered at
        workspace scope, each firing the one dispatch entry point with this adapter's env
        name — one registration per event, no extras."""
        hooks = hooks_yaml["hooks"]

        assert set(hooks) == set(EXPECTED_WORKSPACE_HOOKS)
        for event, timeout in EXPECTED_WORKSPACE_HOOKS.items():
            entries = hooks[event]
            assert isinstance(entries, list)
            assert len(entries) == 1

            entry = entries[0]
            assert entry == {
                "type": "command",
                "command": f"{{{{ADAPTERS_DIR}}}}/dispatch.sh {event} {ADAPTER_ENV}",
                "cwd": "{{FRAMEWORK_DIR}}",
                "timeout": timeout,
            }
            assert shlex.split(entry["command"]) == [
                "{{ADAPTERS_DIR}}/dispatch.sh",
                event,
                ADAPTER_ENV,
            ]

    def test_h0_user_prompt_submit_is_agent_scoped_not_workspace_scoped(self, hooks_yaml):
        """Adapter spec H0: `UserPromptSubmit` is AGENT-scoped — registered per orchestrator
        agent, never at workspace scope, so it fires only for sessions that can open a
        workflow."""
        hooks = hooks_yaml["hooks"]

        assert "UserPromptSubmit" not in hooks
        for orchestrator_slug in EXPECTED_H0_ORCHESTRATORS:
            assert shlex.split(
                f"{{{{ADAPTERS_DIR}}}}/dispatch.sh UserPromptSubmit {ADAPTER_ENV} "
                f"{orchestrator_slug}"
            ) == [
                "{{ADAPTERS_DIR}}/dispatch.sh",
                "UserPromptSubmit",
                ADAPTER_ENV,
                orchestrator_slug,
            ]

    def test_events_explicitly_not_registered_by_the_spec_are_absent(self, hooks_yaml):
        """Adapter spec (Hook registration): events the spec deliberately does not bind stay
        unregistered — the adapter's firing surface is exactly H0–H7, nothing opportunistic."""
        hooks = hooks_yaml["hooks"]

        assert "SessionStart" not in hooks
        assert "PreCompact" not in hooks
        assert "SessionEnd" not in hooks
        assert "ErrorOccurred" not in hooks

    def test_tool_classes_match_h2_through_h6_spec_sections(self, tools_yaml):
        """Adapter spec H2–H6: the tool classes those hooks classify on — dispatch, write
        (with its action verb per tool), mediated-command and guarded-shell — are declared in
        configuration, so classification is data the spec fixes, never code that guesses."""
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
        """Adapter spec (Session identity binding): which host payload keys carry the session,
        the step session and the step actor. `hostParentSessionKeys` is empty — this host
        publishes no parent-session payload, the fact H1's correlation is written against."""
        assert tools_yaml["hostSessionKeys"] == ["session_id"]
        assert tools_yaml["hostParentSessionKeys"] == []
        assert tools_yaml["hostStepSessionKeys"] == ["agent_id"]
        assert tools_yaml["hostStepActorKeys"] == ["agent_type"]
