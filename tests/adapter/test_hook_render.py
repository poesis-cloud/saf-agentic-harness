import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest


ADAPTER_ENV = "vscode-github-copilot-chat"
RENDERED_FILENAME = "safe-harness.json"
EXPECTED_WORKSPACE_HOOKS = {
    "SubagentStart": 60,
    "PreToolUse": 60,
    "PostToolUse": 60,
    "SubagentStop": 10,
    "Stop": 10,
}


def _render(repo_root: Path, *args: str, env_overrides: dict | None = None):
    """Run the renderer exactly as `make install-hooks` does — as a subprocess,
    so exit status and stderr are the real ones the operator sees."""
    env = dict(os.environ)
    env.update(env_overrides or {})
    return subprocess.run(
        [sys.executable, str(repo_root / "adapters" / "render_hooks.py"), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(repo_root),
    )


@pytest.fixture
def framework_root(tmp_path: Path) -> Path:
    """A minimal but COMPLETE framework: one installation renders both targets, so a
    framework the renderer can serve must also name an orchestrator and carry its agent."""
    root = tmp_path / "framework"
    (root / "conf" / "workflows").mkdir(parents=True)
    (root / "conf" / "workflows" / "w0.workflow.conf.yaml").write_text(
        "slug: w0\norchestrator: scrum-master\n", encoding="utf-8"
    )
    (root / "agents").mkdir()
    (root / "agents" / "scrum-master.agent.md").write_text(
        "---\nname: scrum-master\ndescription: 'orchestrator'\n---\n\n# body\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    destination = tmp_path / "dest"
    destination.mkdir()
    return destination


class TestHookRenderer:
    def test_renderer_is_host_agnostic_and_imports_nothing_from_the_harness_source(
        self, repo_root: Path
    ):
        """Adapter spec I15 + (Invocation plumbing, seam 2): the renderer sits beside
        `dispatch.sh` as shared, environment-neutral plumbing — it names no host and
        imports no harness module, so adding a host never edits it."""
        renderer = repo_root / "adapters" / "render_hooks.py"

        assert renderer.is_file()

        source = renderer.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )

        for forbidden in ("services", "stores", "config", "errors", "utils"):
            assert f"import {forbidden}" not in code
            assert f"from {forbidden}" not in code
        assert ADAPTER_ENV not in code

    def test_rendering_leaves_no_unsubstituted_placeholder_in_the_output(
        self, repo_root: Path, framework_root: Path, dest: Path
    ):
        """Adapter spec (Rendered registration): every `{{…}}` placeholder is substituted
        at render time. The installed file is what the host executes verbatim — a
        surviving placeholder is a hook that cannot launch."""
        result = _render(
            repo_root,
            "--env",
            ADAPTER_ENV,
            "--framework-dir",
            str(framework_root),
            "--dest",
            str(dest),
        )

        assert result.returncode == 0, result.stderr

        rendered = (dest / RENDERED_FILENAME).read_text(encoding="utf-8")

        assert "{{" not in rendered
        assert "}}" not in rendered
        assert "FRAMEWORK_DIR" not in rendered
        assert "ADAPTERS_DIR" not in rendered

    def test_rendered_command_is_absolute_and_names_this_harness_dispatch_script(
        self, repo_root: Path, framework_root: Path, dest: Path
    ):
        """Adapter spec (Rendered registration): the `command` is absolutized from the
        RENDERER'S OWN location, never from `FRAMEWORK_DIR` — `adapters/` ships in the
        harness repo, so a framework-anchored command names a file that does not exist."""
        result = _render(
            repo_root,
            "--env",
            ADAPTER_ENV,
            "--framework-dir",
            str(framework_root),
            "--dest",
            str(dest),
            env_overrides={"FRAMEWORK_DIR": "/nonexistent/wrong/anchor"},
        )

        assert result.returncode == 0, result.stderr

        hooks = json.loads((dest / RENDERED_FILENAME).read_text(encoding="utf-8"))["hooks"]

        for event in EXPECTED_WORKSPACE_HOOKS:
            argv = shlex.split(hooks[event][0]["command"])
            dispatch = Path(argv[0])

            assert dispatch.is_absolute()
            assert dispatch.is_file()
            assert dispatch == repo_root / "adapters" / "dispatch.sh"
            assert framework_root not in dispatch.parents
            assert argv[1:] == [event, ADAPTER_ENV]

    def test_rendered_cwd_is_the_absolute_framework_root(
        self, repo_root: Path, framework_root: Path, dest: Path
    ):
        """Adapter spec (Rendered registration): the host resolves a hook `cwd` against
        `$HOME` by default, so every entry pins `cwd` to the resolved absolute framework
        root — the anchor `build_default_adapter()` reads from the environment."""
        result = _render(
            repo_root,
            "--env",
            ADAPTER_ENV,
            "--framework-dir",
            str(framework_root),
            "--dest",
            str(dest),
        )

        assert result.returncode == 0, result.stderr

        hooks = json.loads((dest / RENDERED_FILENAME).read_text(encoding="utf-8"))["hooks"]

        for event in EXPECTED_WORKSPACE_HOOKS:
            cwd = Path(hooks[event][0]["cwd"])

            assert cwd.is_absolute()
            assert cwd.is_dir()
            assert cwd == framework_root.resolve()

    def test_rendered_map_registers_exactly_the_five_workspace_events(
        self, repo_root: Path, framework_root: Path, dest: Path
    ):
        """Adapter spec H1–H7: rendering substitutes paths and nothing else — the
        registered firing surface stays exactly the five workspace events `hooks.yaml`
        declares, each with one command entry and its declared timeout."""
        result = _render(
            repo_root,
            "--env",
            ADAPTER_ENV,
            "--framework-dir",
            str(framework_root),
            "--dest",
            str(dest),
        )

        assert result.returncode == 0, result.stderr

        hooks = json.loads((dest / RENDERED_FILENAME).read_text(encoding="utf-8"))["hooks"]

        assert set(hooks) == set(EXPECTED_WORKSPACE_HOOKS)
        assert "UserPromptSubmit" not in hooks
        for event, timeout in EXPECTED_WORKSPACE_HOOKS.items():
            assert len(hooks[event]) == 1
            assert hooks[event][0]["type"] == "command"
            assert hooks[event][0]["timeout"] == timeout

    def test_default_destination_is_the_framework_workspace_hooks_directory(
        self, repo_root: Path, framework_root: Path
    ):
        """Adapter spec (Rendered registration): the host collects `.github/hooks/*.json`
        from the workspace folder it has open — the framework workspace, not the harness
        checkout — so that is where the file lands when no destination is given."""
        result = _render(
            repo_root,
            "--env",
            ADAPTER_ENV,
            "--framework-dir",
            str(framework_root),
        )

        assert result.returncode == 0, result.stderr
        assert (framework_root / ".github" / "hooks" / RENDERED_FILENAME).is_file()

    def test_the_rendered_file_is_ignored_by_git(self, repo_root: Path):
        """Adapter spec (Rendered registration): the rendered file is machine-specific —
        it pins this checkout's absolute dispatch path and one machine's framework root —
        so it is generated at install time and cannot be committed."""
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", ".github/hooks/safe-harness.json"],
            cwd=str(repo_root),
            capture_output=True,
        )

        assert ignored.returncode == 0

    def test_missing_framework_root_fails_loudly_without_emitting_a_file(
        self, repo_root: Path, dest: Path
    ):
        """Adapter spec (Rendered registration): a half-rendered registration is the
        failure being fixed — with no framework root supplied the renderer refuses and
        writes nothing, rather than emitting a file the host would silently fail on."""
        result = _render(repo_root, "--env", ADAPTER_ENV, "--dest", str(dest))

        assert result.returncode != 0
        assert "framework" in result.stderr.lower()
        assert list(dest.iterdir()) == []

    def test_nonexistent_framework_root_fails_loudly_without_emitting_a_file(
        self, repo_root: Path, tmp_path: Path, dest: Path
    ):
        """Adapter spec (Rendered registration): `cwd` must exist on disk or the host
        cannot launch the hook — a framework root that is not there is rejected before
        anything is written."""
        result = _render(
            repo_root,
            "--env",
            ADAPTER_ENV,
            "--framework-dir",
            str(tmp_path / "absent"),
            "--dest",
            str(dest),
        )

        assert result.returncode != 0
        assert "absent" in result.stderr
        assert list(dest.iterdir()) == []

    def test_a_placeholder_the_renderer_cannot_resolve_aborts_before_writing(
        self, repo_root: Path, framework_root: Path, dest: Path, tmp_path: Path
    ):
        """Adapter spec (Rendered registration): the renderer validates its own output —
        an unknown `{{…}}` placeholder is a rendering stage that is missing, so it fails
        loudly instead of installing a registration the host cannot execute."""
        hooks_source = tmp_path / "hooks.yaml"
        hooks_source.write_text(
            "hooks:\n"
            "  Stop:\n"
            "    - type: command\n"
            "      command: '{{ADAPTERS_DIR}}/dispatch.sh Stop x'\n"
            "      cwd: '{{HARNESS_DIR}}'\n"
            "      timeout: 10\n",
            encoding="utf-8",
        )

        result = _render(
            repo_root,
            "--env",
            ADAPTER_ENV,
            "--framework-dir",
            str(framework_root),
            "--dest",
            str(dest),
            "--hooks",
            str(hooks_source),
        )

        assert result.returncode != 0
        assert "{{HARNESS_DIR}}" in result.stderr
        assert list(dest.iterdir()) == []

    def test_an_entry_missing_a_required_field_aborts_before_writing(
        self, repo_root: Path, framework_root: Path, dest: Path, tmp_path: Path
    ):
        """Adapter spec (Rendered registration): every rendered entry carries `type`,
        `command` and `cwd` — the renderer checks its output against that shape before
        writing, so an incomplete registration never reaches the host."""
        hooks_source = tmp_path / "hooks.yaml"
        hooks_source.write_text(
            "hooks:\n"
            "  Stop:\n"
            "    - type: command\n"
            "      command: '{{ADAPTERS_DIR}}/dispatch.sh Stop x'\n"
            "      timeout: 10\n",
            encoding="utf-8",
        )

        result = _render(
            repo_root,
            "--env",
            ADAPTER_ENV,
            "--framework-dir",
            str(framework_root),
            "--dest",
            str(dest),
            "--hooks",
            str(hooks_source),
        )

        assert result.returncode != 0
        assert "cwd" in result.stderr
        assert list(dest.iterdir()) == []
