import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ADAPTER_ENV = "vscode-github-copilot-chat"
ORCHESTRATOR = "scrum-master"
HOOKS_LOCATION_KEY = "chat.hookFilesLocations"
USE_HOOKS_KEY = "chat.useHooks"

HAND_MAINTAINED = """{
    // A comment the operator wrote — .vscode/settings.json is JSONC.
    "editor.tabSize": 4,
    "chat.hookFilesLocations": {
        ".github/mine": true
    },
    "files.exclude": {
        "**/.git": true
    }
}
"""


def _render(repo_root: Path, *args: str, env_overrides: dict | None = None):
    """Run the renderer exactly as `make install-hooks` does — as a subprocess, so
    exit status and stderr are the real ones the operator sees."""
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
def framework(tmp_path: Path) -> Path:
    """A minimal but COMPLETE framework: one installation renders all three targets, so
    a framework the renderer can serve must also name an orchestrator and carry its
    agent."""
    root = tmp_path / "framework"
    (root / "conf" / "workflows").mkdir(parents=True)
    (root / "conf" / "workflows" / "w0.workflow.conf.yaml").write_text(
        f"slug: w0\norchestrator: {ORCHESTRATOR}\n", encoding="utf-8"
    )
    (root / "agents").mkdir()
    (root / "agents" / f"{ORCHESTRATOR}.agent.md").write_text(
        f"---\nname: {ORCHESTRATOR}\ndescription: 'orchestrator'\n---\n\n# body\n",
        encoding="utf-8",
    )
    return root


def _install(repo_root: Path, framework: Path, **overrides):
    args = ["--env", ADAPTER_ENV, "--framework-dir", str(framework), "--bundle-dir", str(framework)]
    for flag, value in overrides.items():
        args += [f"--{flag.replace('_', '-')}", str(value)]
    return _render(repo_root, *args)


def _settings_file(framework: Path) -> Path:
    return framework / ".vscode" / "settings.json"


def _settings(framework: Path) -> dict:
    text = _settings_file(framework).read_text(encoding="utf-8")
    # The host reads settings as JSONC; the fixtures' comments are the point of the
    # preservation tests, so the oracle drops them before parsing rather than the renderer.
    return json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))


class TestSettingsSource:
    def test_settings_yaml_is_a_render_source_not_an_installable_file(self, adapter_dir: Path):
        """Adapter spec (Required VS Code settings) + (Rendered registration): the third
        render target has a source of truth in this adapter's folder, naming the settings
        file and exactly the settings the host requires — `chat.useHooks` outright, and a
        `chat.hookFilesLocations` entry carrying the `{{HOOKS_LOCATION}}` placeholder no
        checkout can resolve, because where the hooks file lands is an install-time
        choice."""
        source = yaml.safe_load((adapter_dir / "settings.yaml").read_text(encoding="utf-8"))

        assert set(source) == {"settings"}
        assert source["settings"]["path"] == ".vscode/settings.json"
        assert source["settings"]["merge"] == {
            USE_HOOKS_KEY: True,
            HOOKS_LOCATION_KEY: {"{{HOOKS_LOCATION}}": True},
        }

    def test_the_source_never_asserts_the_setting_the_adapter_does_not_require(
        self, adapter_dir: Path
    ):
        """Adapter spec (Required VS Code settings): `chat.useClaudeHooks` is explicitly
        NOT required (`false` acceptable) — this adapter reads Copilot-format hook files
        only. Writing it would impose an unrequired opinion on a hand-maintained file."""
        source = yaml.safe_load((adapter_dir / "settings.yaml").read_text(encoding="utf-8"))

        assert "chat.useClaudeHooks" not in source["settings"]["merge"]


class TestSettingsRender:
    def test_creates_the_settings_file_when_the_workspace_has_none(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Required VS Code settings): `chat.useHooks` is the master switch
        — hook files are discovered but NOT executed without it, so a workspace with no
        settings file at all has every rendered hook inert. The renderer creates it."""
        result = _install(repo_root, framework)

        assert result.returncode == 0, result.stderr
        assert _settings(framework)[USE_HOOKS_KEY] is True

    def test_registers_the_directory_the_hooks_file_actually_landed_in(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Required VS Code settings): `chat.hookFilesLocations` must
        include the hooks directory — the host default `.github/hooks` is only right when
        it is where the file went and the user has not overridden the map."""
        result = _install(repo_root, framework)

        assert result.returncode == 0, result.stderr
        assert _settings(framework)[HOOKS_LOCATION_KEY] == {".github/hooks": True}

    def test_a_hooks_destination_elsewhere_in_the_framework_is_registered_relative(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Installation destination): `HOOKS_DEST` is explicit, so the
        location the host is told to discover must follow the file rather than restate
        the default — a workspace-relative location, which is what the host resolves
        against the workspace folder it has open."""
        result = _install(repo_root, framework, dest=framework / ".github" / "elsewhere")

        assert result.returncode == 0, result.stderr
        assert _settings(framework)[HOOKS_LOCATION_KEY] == {".github/elsewhere": True}

    def test_a_hooks_destination_outside_the_framework_is_registered_absolutely(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Installation destination): a destination outside the framework
        root has no workspace-relative spelling, so the only faithful location is the
        absolute one — restating `.github/hooks` there would name a file that is not
        where the renderer put it."""
        outside = tmp_path / "outside-hooks"
        outside.mkdir()

        result = _install(repo_root, framework, dest=outside)

        assert result.returncode == 0, result.stderr
        assert _settings(framework)[HOOKS_LOCATION_KEY] == {outside.resolve().as_posix(): True}

    def test_preserves_every_unrelated_key_and_the_files_own_formatting(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Rendered registration): `.vscode/settings.json` is a committed,
        hand-maintained file — unlike the two generated targets. Only the required keys
        are set; every other key, the operator's comments and the file's own indentation
        survive the merge untouched."""
        settings = _settings_file(framework)
        settings.parent.mkdir(parents=True)
        settings.write_text(HAND_MAINTAINED, encoding="utf-8")

        result = _install(repo_root, framework)

        assert result.returncode == 0, result.stderr

        text = settings.read_text(encoding="utf-8")

        assert "// A comment the operator wrote" in text
        assert '    "editor.tabSize": 4,\n' in text
        assert '    "files.exclude": {\n        "**/.git": true\n    }\n' in text

    def test_merges_into_an_existing_hook_locations_map_instead_of_replacing_it(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Required VS Code settings): the map is a discovery set the host
        reads in full — the adapter's entry is ADDED to it. Replacing it would silently
        un-register every other hook location the operator relies on."""
        settings = _settings_file(framework)
        settings.parent.mkdir(parents=True)
        settings.write_text(HAND_MAINTAINED, encoding="utf-8")

        result = _install(repo_root, framework)

        assert result.returncode == 0, result.stderr
        assert _settings(framework)[HOOKS_LOCATION_KEY] == {
            ".github/mine": True,
            ".github/hooks": True,
        }
        assert _settings(framework)["editor.tabSize"] == 4

    def test_overrides_a_use_hooks_value_the_host_would_read_as_off(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Required VS Code settings): `chat.useHooks` has ONE required
        value. Left at `false` every rendered hook is discovered and never executed, so
        installation must correct it rather than leave the whole binding inert."""
        settings = _settings_file(framework)
        settings.parent.mkdir(parents=True)
        settings.write_text('{\n  "chat.useHooks": false\n}\n', encoding="utf-8")

        result = _install(repo_root, framework)

        assert result.returncode == 0, result.stderr
        assert _settings(framework)[USE_HOOKS_KEY] is True

    def test_sets_keys_without_claiming_ownership_of_the_file(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Rendered registration): unlike the H0 block, these keys carry no
        managed delimiter. The block is a generated artifact the renderer owns and may
        strip; these are individual host settings in a file the operator owns, and a
        delimiter would license removing a value they deliberately set."""
        result = _install(repo_root, framework)

        assert result.returncode == 0, result.stderr

        text = _settings_file(framework).read_text(encoding="utf-8")

        assert "safe-harness" not in text
        assert ">>>" not in text

    def test_rendering_twice_is_idempotent(self, repo_root: Path, framework: Path):
        """Adapter spec (Rendering guarantees): re-running changes nothing but what
        moved. A second installation must not accumulate a second copy of a key, nor
        rewrite a file whose required settings are already correct."""
        settings = _settings_file(framework)
        settings.parent.mkdir(parents=True)
        settings.write_text(HAND_MAINTAINED, encoding="utf-8")

        assert _install(repo_root, framework).returncode == 0

        once = settings.read_bytes()

        assert _install(repo_root, framework).returncode == 0

        assert settings.read_bytes() == once
        assert settings.read_text(encoding="utf-8").count(f'"{USE_HOOKS_KEY}"') == 1
        assert settings.read_text(encoding="utf-8").count(f'"{HOOKS_LOCATION_KEY}"') == 1


class TestSettingsRefusals:
    def test_a_malformed_settings_file_is_refused_rather_than_replaced(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Rendering guarantees): rendering is all-or-nothing and refuses
        rather than corrupting. A settings file the renderer cannot parse is a file whose
        contents it cannot preserve — replacing it would discard settings the operator
        depends on and cannot recover."""
        settings = _settings_file(framework)
        settings.parent.mkdir(parents=True)
        settings.write_text('{\n  "editor.tabSize": 4,\n  "broken"\n', encoding="utf-8")

        before = settings.read_bytes()
        result = _install(repo_root, framework)

        assert result.returncode != 0
        assert "settings" in result.stderr.lower()
        assert settings.read_bytes() == before

    def test_a_settings_file_that_is_not_an_object_is_refused(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Required VS Code settings): settings are a key/value object. A
        file holding anything else has no place to carry a setting, and overwriting it
        would destroy whatever the operator meant by it."""
        settings = _settings_file(framework)
        settings.parent.mkdir(parents=True)
        settings.write_text("[1, 2, 3]\n", encoding="utf-8")

        before = settings.read_bytes()
        result = _install(repo_root, framework)

        assert result.returncode != 0
        assert settings.read_bytes() == before

    def test_a_hook_locations_value_the_host_discards_is_refused_not_overwritten(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Required VS Code settings): the host reads
        `chat.hookFilesLocations` as an object and discards any other shape. Merging into
        a value that is not one is impossible, and silently replacing the operator's
        value is the clobbering this target exists to avoid — so it is reported."""
        settings = _settings_file(framework)
        settings.parent.mkdir(parents=True)
        settings.write_text(
            '{\n  "chat.hookFilesLocations": [".github/hooks"]\n}\n', encoding="utf-8"
        )

        before = settings.read_bytes()
        result = _install(repo_root, framework)

        assert result.returncode != 0
        assert HOOKS_LOCATION_KEY in result.stderr
        assert settings.read_bytes() == before

    def test_no_settings_are_written_when_another_target_refuses(
        self, repo_root: Path, framework: Path
    ):
        """Adapter spec (Rendering guarantees): rendering is all-or-nothing ACROSS the
        targets. Enabling `chat.useHooks` while the hooks file was never written points
        the host at a registration that does not exist."""
        (framework / "agents" / f"{ORCHESTRATOR}.agent.md").write_text(
            "# no frontmatter here\n", encoding="utf-8"
        )

        result = _install(repo_root, framework)

        assert result.returncode != 0
        assert not _settings_file(framework).exists()
        assert not (framework / ".github" / "hooks").exists()
