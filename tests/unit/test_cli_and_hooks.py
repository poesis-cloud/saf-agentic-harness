"""Unit tests — the CLI composition root: Application (parser + dispatch wiring + fail-fast
config) and Command. HookService's event normalization is unit-tested here too (its heavier
funnel behavior stays in tests/integration/test_hooks.py)."""

from __future__ import annotations

import pytest

from cli.application import Application, COMMANDS, main
from config import ConfigError
from mappers import Workspace
from services import HookService


class TestCommand:
    def test_registry_declares_the_four_core_commands(self):
        assert [c.name for c in COMMANDS] == ["check-artifact", "check-step", "hook", "orchestrate"]


class TestApplication:
    def test_build_parser_registers_every_command(self):
        parser = Application.build_parser()
        args = parser.parse_args(["check-artifact"])
        assert args.command == "check-artifact"
        args = parser.parse_args(["orchestrate", "--workflow", "team", "--unit", "u-1"])
        assert args.workflow == "team"

    def test_command_is_required(self, capsys):
        with pytest.raises(SystemExit):
            Application.build_parser().parse_args([])

    def test_init_builds_the_validated_config_plane(self):
        app = Application(Workspace.detect())
        assert app.config.model_profiles.models()
        assert app.router.profiles is app.config.model_profiles
        assert set(app._runners) == {c.name for c in COMMANDS}

    def test_main_fails_fast_on_invalid_configuration(self, tmp_path, monkeypatch, capsys):
        # A framework root with contracts but no conf/ -> ConfigError -> non-zero exit, findings rendered.
        (tmp_path / "harness" / "contracts").mkdir(parents=True)
        monkeypatch.setattr(Workspace, "default_framework_root", classmethod(lambda cls: tmp_path))
        rc = main(["check-artifact"])
        assert rc == 1
        assert "missing framework configuration file" in capsys.readouterr().err

    def test_main_orchestrate_emits_action_json(self, capsys):
        rc = main(["orchestrate", "--workflow", "verification", "--unit", "demo"])
        out = capsys.readouterr().out
        assert rc == 0
        assert '"action"' in out


class TestHookService:
    def _service(self) -> HookService:
        app = Application(Workspace.detect())
        return HookService(app.workspace, app.schemas, app.logs,
                           app.config.access_control_list, app.config.workspace_layout,
                           app.config.workflows, app.router,
                           binding=app.config.adapter_binding("github-copilot"),
                           artifacts=app.artifacts)

    def test_events_normalize_to_workflow_phases(self):
        svc = self._service()
        assert svc.handle("userPromptSubmit", {}).phase == "observe"
        assert svc.handle("unknownEvent", {}).phase == "observe"
        assert svc.handle("stop", {}).phase == "session-close"

    def test_precondition_without_write_allows(self):
        decision = self._service().handle("preToolUse", {"tool": "someReadTool", "tool_input": {}})
        assert decision.permission == "allow"
        assert decision.outputs == []

    def test_write_without_actor_is_denied_reasoned(self):
        svc = self._service()
        write_tool = next(iter(svc.binding.get("write_tools", {"write": "create"})))
        decision = svc.handle("preToolUse", {"tool": write_tool, "tool_input": {"path": "portfolio/x.md"}})
        assert "no actor" in decision.reason

    def test_dispatch_governance_denies_auto_and_unknown_models(self):
        svc = self._service()
        dispatch_tool = (svc.binding.get("dispatch_tools") or ["runSubagent"])[0]

        def decide(model):
            tool_input = {"agentName": "developer"}
            if model is not None:
                tool_input["model"] = model
            return svc.handle("preToolUse", {"tool": dispatch_tool, "tool_input": tool_input})

        assert decide("Auto").permission == "deny"
        assert decide(None).permission == "deny"
        assert decide("no-such-model").permission == "deny"
        known = next(iter(svc.router.profiles.models()))
        assert decide(known).permission == "allow"

    def test_postcondition_auto_runs_check_artifact_only(self):
        svc = self._service()
        assert svc.commands_for("postcondition") == ["check-artifact"]
        assert svc.commands_for("precondition") == []
        assert svc.commands_for("session-open") == []
