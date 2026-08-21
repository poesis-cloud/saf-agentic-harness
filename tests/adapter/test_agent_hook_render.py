import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ADAPTER_ENV = "vscode-github-copilot-chat"
ORCHESTRATORS = ("value-management-officer", "release-train-engineer", "scrum-master")
BENCH_AGENT = "developer"
AGENT_BODY = (
    "\n"
    "<!-- Copyright 2026 Poesis Cloud and contributors -->\n"
    "\n"
    "# {slug}\n"
    "\n"
    "Body text with a trailing space \n"
    "and a second line.\n"
)


def _agent_file(slug: str) -> str:
    return (
        "---\n"
        f"name: {slug}\n"
        f"description: 'The {slug} — one line, quoted, with a — dash.'\n"
        "---\n" + AGENT_BODY.format(slug=slug)
    )


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
    """A hermetic stand-in for a framework checkout: its own workflow catalog and its
    own committed, host-agnostic agent sources. Never the real framework repo."""
    root = tmp_path / "framework"
    workflows = root / "conf" / "workflows"
    workflows.mkdir(parents=True)
    agents = root / "agents"
    agents.mkdir()

    for index, slug in enumerate(ORCHESTRATORS):
        (workflows / f"w{index}.workflow.conf.yaml").write_text(
            f"slug: w{index}\norchestrator: {slug}\n", encoding="utf-8"
        )
    # a second workflow for the same orchestrator — the catalog is a multiset, the
    # registration is per agent
    (workflows / "w-extra.workflow.conf.yaml").write_text(
        f"slug: w-extra\norchestrator: {ORCHESTRATORS[0]}\n", encoding="utf-8"
    )

    for slug in (*ORCHESTRATORS, BENCH_AGENT):
        (agents / f"{slug}.agent.md").write_text(_agent_file(slug), encoding="utf-8")

    return root


def _install(repo_root: Path, framework: Path, tmp_path: Path, env_overrides=None, **overrides):
    args = [
        "--env",
        ADAPTER_ENV,
        "--framework-dir",
        str(framework),
        "--dest",
        str(tmp_path / "hooks-dest"),
    ]
    for flag, value in overrides.items():
        args += [f"--{flag.replace('_', '-')}", str(value)]
    return _render(repo_root, *args, env_overrides=env_overrides)


def _rendered(framework: Path, slug: str) -> Path:
    return framework / ".github" / "agents" / f"{slug}.agent.md"


def _frontmatter(text: str) -> dict:
    _, _, rest = text.partition("---\n")
    front, _, _ = rest.partition("\n---\n")
    return yaml.safe_load(front)


class TestAgentHooksSource:
    def test_agent_hooks_yaml_is_a_render_source_not_an_installable_block(self, adapter_dir: Path):
        """Adapter spec H0 (Registration) + (Rendered registration): `agent-hooks.yaml` is the
        source of truth for the session-started boundary — one `UserPromptSubmit` entry
        carrying the deployment placeholders no checkout can resolve, including the
        `{{AGENT_SLUG}}` that makes the registration per orchestrator."""
        source = yaml.safe_load((adapter_dir / "agent-hooks.yaml").read_text(encoding="utf-8"))
        entries = source["agentHooks"]["UserPromptSubmit"]

        assert set(source) == {"agentHooks"}
        assert set(source["agentHooks"]) == {"UserPromptSubmit"}
        assert entries == [
            {
                "type": "command",
                "command": (
                    "{{ADAPTERS_DIR}}/dispatch.sh UserPromptSubmit "
                    f"{ADAPTER_ENV} {{{{AGENT_SLUG}}}}"
                ),
                "cwd": "{{FRAMEWORK_DIR}}",
                "timeout": 30,
            }
        ]
        assert shlex.split(entries[0]["command"])[-1] == "{{AGENT_SLUG}}"


class TestAgentScopedHookRender:
    def test_each_orchestrator_is_registered_with_its_own_slug_as_dispatch_argument(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Registration): the session-started boundary is an AGENT-scoped
        `UserPromptSubmit` hook in each orchestrator's `.agent.md` frontmatter, the scoping
        agent's slug passed as the trailing dispatch argument — host-fixed, not
        model-authored, since no top-level payload names the active agent."""
        result = _install(repo_root, framework, tmp_path)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            entries = _frontmatter(_rendered(framework, slug).read_text(encoding="utf-8"))[
                "hooks"
            ]["UserPromptSubmit"]

            assert len(entries) == 1
            assert entries[0]["type"] == "command"
            assert entries[0]["timeout"] == 30
            assert shlex.split(entries[0]["command"])[1:] == [
                "UserPromptSubmit",
                ADAPTER_ENV,
                slug,
            ]

    def test_a_non_orchestrator_agent_is_never_registered(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec H0: C7 holds structurally — the hook fires only while a framework
        ORCHESTRATOR is the active agent, so the bench carries no registration at all and
        no hook can fire for a foreign agent."""
        result = _install(repo_root, framework, tmp_path)

        assert result.returncode == 0, result.stderr
        assert not _rendered(framework, BENCH_AGENT).exists()
        assert sorted(path.name for path in (framework / ".github" / "agents").iterdir()) == sorted(
            f"{slug}.agent.md" for slug in ORCHESTRATORS
        )

    def test_the_orchestrator_set_is_derived_from_the_frameworks_own_workflow_catalog(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Preconditions): the scoping slug must resolve to a framework
        orchestrator. The framework's workflow catalog is what defines one, so the
        registration follows the catalog rather than a list the adapter hardcodes."""
        (framework / "conf" / "workflows" / "w-new.workflow.conf.yaml").write_text(
            "slug: w-new\norchestrator: product-manager\n", encoding="utf-8"
        )
        (framework / "agents" / "product-manager.agent.md").write_text(
            _agent_file("product-manager"), encoding="utf-8"
        )

        result = _install(repo_root, framework, tmp_path)

        assert result.returncode == 0, result.stderr
        assert _rendered(framework, "product-manager").is_file()

    def test_the_committed_agent_source_is_never_modified(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendered registration): the rendered registration is
        machine-specific — it pins this checkout's absolute dispatch path — so it lands in
        a generated artifact. The framework's committed agent sources stay byte-for-byte
        host-agnostic and machine-independent."""
        before = {
            path: path.read_bytes() for path in (framework / "agents").glob("*.agent.md")
        }

        result = _install(repo_root, framework, tmp_path)

        assert result.returncode == 0, result.stderr
        assert {path: path.read_bytes() for path in before} == before

    def test_existing_frontmatter_and_body_survive_the_injection_unchanged(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Registration): the block is ADDED to the orchestrator's own
        frontmatter — `name`, `description` and the agent's instructions are the agent's
        identity and must reach the host exactly as authored."""
        result = _install(repo_root, framework, tmp_path)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            source = (framework / "agents" / f"{slug}.agent.md").read_text(encoding="utf-8")
            rendered = _rendered(framework, slug).read_text(encoding="utf-8")

            assert rendered.endswith(AGENT_BODY.format(slug=slug))
            assert f"name: {slug}\n" in rendered
            assert _frontmatter(rendered)["description"] == _frontmatter(source)["description"]
            assert _frontmatter(rendered)["name"] == _frontmatter(source)["name"]

    def test_no_placeholder_survives_in_a_rendered_agent_file(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendered registration): every `{{…}}` is substituted at render
        time — the host executes the registered command verbatim, so a surviving
        placeholder is a hook that cannot launch."""
        result = _install(repo_root, framework, tmp_path)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            rendered = _rendered(framework, slug).read_text(encoding="utf-8")

            assert "{{" not in rendered
            assert "}}" not in rendered

    def test_the_command_is_absolutized_from_the_renderers_own_location(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees, decision C): the `command` is absolutized
        from the RENDERER'S own location, never from `FRAMEWORK_DIR` — `adapters/` ships
        in the harness repo, so a framework-anchored command names nothing."""
        result = _install(
            repo_root,
            framework,
            tmp_path,
            env_overrides={"FRAMEWORK_DIR": "/nonexistent/wrong/anchor"},
        )

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            entry = _frontmatter(_rendered(framework, slug).read_text(encoding="utf-8"))[
                "hooks"
            ]["UserPromptSubmit"][0]
            dispatch = Path(shlex.split(entry["command"])[0])

            assert dispatch == repo_root / "adapters" / "dispatch.sh"
            assert dispatch.is_file()
            assert framework not in dispatch.parents
            assert Path(entry["cwd"]) == framework.resolve()

    def test_rendering_twice_is_idempotent(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): installation is re-run whenever either
        checkout moves, so a second run must produce the same registration — never a
        second `hooks:` key the host would read as a duplicate."""
        assert _install(repo_root, framework, tmp_path).returncode == 0

        first = {slug: _rendered(framework, slug).read_bytes() for slug in ORCHESTRATORS}

        assert _install(repo_root, framework, tmp_path).returncode == 0

        for slug in ORCHESTRATORS:
            rendered = _rendered(framework, slug)

            assert rendered.read_bytes() == first[slug]
            assert rendered.read_text(encoding="utf-8").count("UserPromptSubmit:") == 1

    def test_rendering_in_place_twice_never_duplicates_the_block(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): the destination is explicit, and an operator
        whose host reads the agents it renders in place gets the block written back over an
        agent that already carries one — a second `hooks:` key is a frontmatter the host's
        own validation rejects, so the managed block is replaced, never appended."""
        agents = framework / "agents"

        assert _install(repo_root, framework, tmp_path, agents_dest=agents).returncode == 0

        once = (agents / f"{ORCHESTRATORS[0]}.agent.md").read_bytes()

        assert _install(repo_root, framework, tmp_path, agents_dest=agents).returncode == 0

        rendered = (agents / f"{ORCHESTRATORS[0]}.agent.md").read_text(encoding="utf-8")

        assert (agents / f"{ORCHESTRATORS[0]}.agent.md").read_bytes() == once
        assert rendered.count("UserPromptSubmit:") == 1
        assert rendered.count("hooks:") == 1

    def test_a_stale_managed_block_is_replaced_not_appended(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): the installed registration is invalidated
        whenever either checkout moves — re-rendering over a block that names a stale
        dispatch path must REPLACE it, or the host keeps firing a command that is gone."""
        agents = framework / "agents"

        assert _install(repo_root, framework, tmp_path, agents_dest=agents).returncode == 0

        stale = agents / f"{ORCHESTRATORS[0]}.agent.md"
        stale.write_text(
            stale.read_text(encoding="utf-8").replace(
                str(repo_root / "adapters"), "/moved/elsewhere/adapters"
            ),
            encoding="utf-8",
        )

        assert _install(repo_root, framework, tmp_path, agents_dest=agents).returncode == 0

        rendered = stale.read_text(encoding="utf-8")

        assert "/moved/elsewhere/adapters" not in rendered
        assert rendered.count("UserPromptSubmit:") == 1
        assert rendered.endswith(AGENT_BODY.format(slug=ORCHESTRATORS[0]))

    def test_an_agent_without_frontmatter_fails_loudly_and_writes_nothing(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): rendering is all-or-nothing. An agent file
        with no frontmatter has nowhere to carry the block — the renderer refuses rather
        than inventing one and corrupting the agent."""
        (framework / "agents" / f"{ORCHESTRATORS[0]}.agent.md").write_text(
            "# no frontmatter here\n", encoding="utf-8"
        )

        result = _install(repo_root, framework, tmp_path)

        assert result.returncode != 0
        assert "frontmatter" in result.stderr.lower()
        assert not (framework / ".github" / "agents").exists()

    def test_an_agent_with_unclosed_frontmatter_fails_loudly_and_writes_nothing(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): the output is validated in memory and
        written only if it passes — an unterminated frontmatter block cannot be parsed,
        so nothing is emitted for any agent."""
        (framework / "agents" / f"{ORCHESTRATORS[1]}.agent.md").write_text(
            "---\nname: x\ndescription: 'y'\n\n# body\n", encoding="utf-8"
        )

        result = _install(repo_root, framework, tmp_path)

        assert result.returncode != 0
        assert "frontmatter" in result.stderr.lower()
        assert not (framework / ".github" / "agents").exists()

    def test_an_agent_declaring_its_own_hooks_block_is_refused(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Registration): the block is the adapter's to own. An agent that
        already declares an unmanaged `hooks:` key cannot receive a second one — YAML would
        carry a duplicate key and the host's own frontmatter validation would reject it."""
        agent = framework / "agents" / f"{ORCHESTRATORS[2]}.agent.md"
        agent.write_text(
            agent.read_text(encoding="utf-8").replace(
                "---\n" + AGENT_BODY.format(slug=ORCHESTRATORS[2]),
                "hooks:\n  Stop:\n    - type: command\n      command: './x.sh'\n"
                "---\n" + AGENT_BODY.format(slug=ORCHESTRATORS[2]),
            ),
            encoding="utf-8",
        )

        result = _install(repo_root, framework, tmp_path)

        assert result.returncode != 0
        assert "hooks" in result.stderr.lower()
        assert not (framework / ".github" / "agents").exists()

    def test_an_orchestrator_with_no_agent_file_aborts_before_writing_any_agent(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Preconditions): a scoping slug that resolves to no agent is a
        `configuration-error`. Rendering is all-or-nothing — a partial install would leave
        some orchestrators registered and others silently inert, the exact H0 gap."""
        (framework / "agents" / f"{ORCHESTRATORS[0]}.agent.md").unlink()

        result = _install(repo_root, framework, tmp_path)

        assert result.returncode != 0
        assert ORCHESTRATORS[0] in result.stderr
        assert not (framework / ".github" / "agents").exists()

    def test_a_framework_with_no_orchestrator_is_refused(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec H0: without a registration the session-started boundary never
        fires and no session is ever opened. A framework whose catalog names no
        orchestrator is a misconfiguration to report, not an empty install to accept."""
        for path in (framework / "conf" / "workflows").glob("*.yaml"):
            path.unlink()

        result = _install(repo_root, framework, tmp_path)

        assert result.returncode != 0
        assert "orchestrator" in result.stderr.lower()

    def test_a_corrupt_managed_block_fails_loudly_rather_than_being_patched(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): a half-rendered registration is the failure
        this stage exists to prevent — a managed block whose delimiters were damaged is
        reported, never silently rewritten around."""
        agents = framework / "agents"

        assert _install(repo_root, framework, tmp_path, agents_dest=agents).returncode == 0

        target = agents / f"{ORCHESTRATORS[0]}.agent.md"
        damaged = "".join(
            line
            for line in target.read_text(encoding="utf-8").splitlines(keepends=True)
            if "<<<" not in line
        )
        target.write_text(damaged, encoding="utf-8")

        result = _install(repo_root, framework, tmp_path, agents_dest=agents)

        assert result.returncode != 0
        assert "block" in result.stderr.lower()
        assert target.read_text(encoding="utf-8") == damaged

    def test_the_rendered_agent_files_are_ignored_by_git(self, repo_root: Path):
        """Adapter spec (Rendering guarantees): the rendered registration pins this
        checkout's absolute dispatch path and one machine's framework root — it is
        generated at install time and cannot be committed, wherever it lands."""
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", ".github/agents/scrum-master.agent.md"],
            cwd=str(repo_root),
            capture_output=True,
        )

        assert ignored.returncode == 0

    def test_the_registered_command_is_emitted_on_one_line(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec H0 (Registration) + host facts: the host executes `command` as a
        shell command line. Emitting it as a YAML-folded multi-line scalar leaves the
        registration correct only if the host's frontmatter parser folds it back — an
        avoidable bet on a preview feature, taken on every command over 80 columns."""
        result = _install(repo_root, framework, tmp_path)

        assert result.returncode == 0, result.stderr

        for slug in ORCHESTRATORS:
            lines = _rendered(framework, slug).read_text(encoding="utf-8").splitlines()
            commands = [line for line in lines if line.strip().startswith("command:")]

            assert len(commands) == 1
            assert commands[0].strip().endswith(slug)

    def test_a_failure_on_one_agent_leaves_every_other_agent_untouched(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendering guarantees): rendering is all-or-nothing across the whole
        registration — an agent that renders before the failing one must not be rewritten,
        or the install is exactly the half-applied state this stage exists to prevent."""
        agents = framework / "agents"

        assert _install(repo_root, framework, tmp_path, agents_dest=agents).returncode == 0

        first, *_, last = sorted(ORCHESTRATORS)
        # `first` is rendered before `last` fails, and its pending output DIFFERS from what
        # is on disk — so a renderer that writes as it goes would visibly rewrite it.
        early = agents / f"{first}.agent.md"
        early.write_text(
            early.read_text(encoding="utf-8").replace(
                str(repo_root / "adapters"), "/moved/elsewhere/adapters"
            ),
            encoding="utf-8",
        )
        stale = early.read_bytes()
        (agents / f"{last}.agent.md").write_text("---\nname: broken\n", encoding="utf-8")

        result = _install(repo_root, framework, tmp_path, agents_dest=agents)

        assert result.returncode != 0
        assert early.read_bytes() == stale

    def test_the_workspace_hooks_file_is_still_rendered_by_the_same_run(
        self, repo_root: Path, framework: Path, tmp_path: Path
    ):
        """Adapter spec (Rendered registration): both render targets come from this
        adapter's sources and one installation stage — `make install-hooks` installs the
        workspace file AND the agent-scoped H0 blocks, or the boundary set is incomplete."""
        result = _install(repo_root, framework, tmp_path)

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "hooks-dest" / "safe-harness.json").is_file()
        assert _rendered(framework, ORCHESTRATORS[0]).is_file()
