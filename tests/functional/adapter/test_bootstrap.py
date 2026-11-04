"""Hook bootstrap end to end — a hook runs from its rendered registration alone.

The host runs a hook with ITS environment, not the operator's shell: no activated venv, no
exported `FRAMEWORK_*`, possibly no `python3` on `PATH` at all. These tests fire the real
`dispatch.sh` under exactly that — a `PATH` carrying only `bash` and `dirname`, plus the
`env` the rendered entry records (`FRAMEWORK_DIR`, `SAF_HARNESS_PYTHON`) — and assert that it
bootstraps, or that it fails CLOSED and VISIBLY (exit 2, diagnostic on stderr) rather than
with an exit the host would treat as non-blocking and fail open on.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Mapping

import pytest
import yaml

from harness_stub import HarnessStub
from hook_dispatch import HookRun, run_hook
from posix_shell import IS_WINDOWS, find_git_bash
from host_events import ORCHESTRATOR_AGENT, pre_tool_use, user_prompt_submit
from stub_framework import INSTRUCTION_BODY, Framework

_LAYOUT_ENV_FILE = (
    "FRAMEWORK_WORKSPACE_DIR=workspace\n"
    "FRAMEWORK_INSTRUCTIONS_DIR=instructions\n"
    "FRAMEWORK_SKILLS_DIR=skills\n"
)


@pytest.fixture
def bare_path(tmp_path: Path) -> str:
    """A `PATH` with no interpreter on it: only what `dispatch.sh` itself needs."""
    bin_dir = tmp_path / "bare-bin"
    bin_dir.mkdir()
    located = {}
    for program in ("bash", "dirname"):
        found = find_git_bash() if program == "bash" else shutil.which(program)
        if found is None and IS_WINDOWS:
            pytest.skip(f"Git for Windows {program} is required to run dispatch.sh")
        assert found is not None, f"{program} is required to run dispatch.sh"
        located[program] = found
    try:
        for program, found in located.items():
            (bin_dir / program).symlink_to(found)
    except OSError:
        # Windows refuses unprivileged symlinks: put the programs' own directories on
        # PATH instead (Git for Windows ships no Python beside bash or dirname).
        return os.pathsep.join(dict.fromkeys(str(Path(found).parent) for found in located.values()))
    return str(bin_dir)


@pytest.fixture
def registration_env(framework: Framework, bare_path: str) -> dict[str, str]:
    """The environment a host builds from a rendered entry: its `env`, nothing exported."""
    (framework.root / ".env").write_text(_LAYOUT_ENV_FILE, encoding="utf-8")
    inherited = framework.environment
    return {
        "PATH": bare_path,
        "HOME": inherited["HOME"],
        "XDG_STATE_HOME": inherited["XDG_STATE_HOME"],
        "HARNESS_STUB_SCRIPT": inherited["HARNESS_STUB_SCRIPT"],
        "HARNESS_STUB_JOURNAL": inherited["HARNESS_STUB_JOURNAL"],
        "FRAMEWORK_DIR": str(framework.root),
        "SAF_HARNESS_PYTHON": sys.executable,
    }


def _fire(
    framework: Framework,
    environment: Mapping[str, str],
    event: str = "UserPromptSubmit",
    payload: Mapping | None = None,
    agent: str | None = ORCHESTRATOR_AGENT,
) -> HookRun:
    return run_hook(
        dispatch_path=framework.dispatch_path,
        event=event,
        payload=payload if payload is not None else user_prompt_submit(),
        environment=environment,
        agent=agent,
        cwd=framework.root,
    )


class TestHookBootstrap:
    """Adapter spec — Rendered registration (bootstrap) and Bootstrap failure."""

    def test_the_registration_env_alone_bootstraps_the_session_started_hook(
        self,
        framework: Framework,
        registration_env: dict[str, str],
        harness_stub: HarnessStub,
    ) -> None:
        """The rendered `env` is sufficient: the recorded interpreter runs the adapter AND
        the harness, and the layout comes from `FRAMEWORK_DIR/.env` — no `python3` on
        `PATH`, no exported layout variable."""
        run = _fire(framework, registration_env)

        assert run.exit_code == 0, run.stderr
        assert INSTRUCTION_BODY in run.decision()["hookSpecificOutput"]["additionalContext"]
        assert harness_stub.functions[0] == "start-session"

    def test_a_registration_without_its_interpreter_fails_closed_visibly(
        self, framework: Framework, registration_env: dict[str, str]
    ) -> None:
        """No interpreter reachable: exit 2 with a diagnostic, never the host's
        non-blocking exit 127 under which the prompt would proceed ungoverned."""
        del registration_env["SAF_HARNESS_PYTHON"]

        run = _fire(framework, registration_env)

        assert run.exit_code == 2
        assert run.stdout == ""
        assert "failing closed" in run.stderr

    def test_a_registration_without_the_framework_anchor_fails_closed_visibly(
        self,
        framework: Framework,
        registration_env: dict[str, str],
        harness_stub: HarnessStub,
    ) -> None:
        """No `FRAMEWORK_DIR`: the adapter refuses to guess the governed tree — exit 2,
        diagnostic naming the variable, and no harness function ever invoked."""
        del registration_env["FRAMEWORK_DIR"]

        run = _fire(framework, registration_env)

        assert run.exit_code == 2
        assert "FRAMEWORK_DIR" in run.stderr
        assert harness_stub.calls == ()

    def test_a_binding_the_adapter_contract_rejects_fails_closed_visibly(
        self,
        framework: Framework,
        registration_env: dict[str, str],
        harness_stub: HarnessStub,
    ) -> None:
        """A malformed `tools.yaml` (here: `pathKeys` a string, not a list) is refused at load —
        exit 2 naming the violated contract, instead of degrading into a binding that
        classifies every write as a pass-through."""
        tools_path = framework.root / "adapters" / "vscode-github-copilot-chat" / "tools.yaml"
        tools = yaml.safe_load(tools_path.read_text(encoding="utf-8"))
        tools["pathKeys"] = "filePath"
        tools_path.write_text(yaml.safe_dump(tools), encoding="utf-8")

        run = _fire(
            framework,
            registration_env,
            event="PreToolUse",
            payload=pre_tool_use("create_file", {"filePath": "portfolio/a.md"}),
            agent=None,
        )

        assert run.exit_code == 2
        assert "tools.conf.schema.json" in run.stderr
        assert harness_stub.calls == ()
